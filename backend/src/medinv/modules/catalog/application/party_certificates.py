import json
from collections.abc import Sequence
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.party_certificates import (
    PARTY_CERTIFICATE_FIELDS,
    PartyCertificateCreate,
    PartyCertificateReview,
    PartyCertificateRevision,
)
from medinv.modules.catalog.application.attachments import attachment_record
from medinv.modules.catalog.application.change_bundle import (
    certificate_rows,
    editable,
    pending,
    stage_certificate,
)
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

TYPES = {
    "BUSINESS_LICENSE": "营业执照",
    "DRUG_BUSINESS_LICENSE": "药品经营许可证",
    "DRUG_PRODUCTION_LICENSE": "药品生产许可证",
    "DEVICE_BUSINESS_LICENSE": "医疗器械经营许可证",
    "DEVICE_BUSINESS_FILING": "医疗器械经营备案",
    "DEVICE_PRODUCTION_LICENSE": "医疗器械生产许可证",
    "DEVICE_PRODUCTION_FILING": "医疗器械生产备案",
    "MEDICAL_INSTITUTION_LICENSE": "医疗机构执业许可证",
    "AUTHORIZATION": "授权书 / 授权联系人",
    "OTHER": "其他证照",
}
USES = {"SUPPLY": "作为供应商供货", "PURCHASE": "作为客户购货"}


def compatibility(party: dict[str, Any], uses: Sequence[str]) -> str:
    allowed = {"SUPPLIER": {"SUPPLY"}, "CUSTOMER": {"PURCHASE"}, "BOTH": {"SUPPLY", "PURCHASE"}}[
        party["kind"]
    ]
    return "MATCH" if set(uses) <= allowed else "MISMATCH"


def snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return dict(jsonable_encoder({k: row[k] for k in (*PARTY_CERTIFICATE_FIELDS, "state")}))


def today(repo: SqlSession) -> date:
    return cast(
        date,
        repo.rows("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS day", {})[0][
            "day"
        ],
    )


def present(row: dict[str, Any], party: dict[str, Any], day: date) -> dict[str, Any]:
    validity = row["validity_mode"]
    if validity == "FIXED":
        validity = (
            "UPCOMING"
            if row["valid_from"] > day
            else "EXPIRED"
            if row["valid_until"] < day
            else "IN_PERIOD"
        )
    return dict(
        jsonable_encoder(
            {
                **row,
                "version": str(row["version"]),
                "validity": validity,
                "compatibility": compatibility(party, row["business_uses"]),
            }
        )
    )


def record(repo: SqlSession, party: UUID, certificate: UUID, lock: bool = False) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.party_certificate WHERE party_id=:party AND id=:id"
        + (" FOR UPDATE" if lock else ""),
        {"party": party, "id": certificate},
    )
    if not lock:
        rows = [r for r in certificate_rows(repo, "party", party, rows) if r["id"] == certificate]
    if not rows:
        raise BusinessError("NOT_FOUND", "证照不可见或不属于此往来单位", 404)
    return rows[0]


class PartyCertificates:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def scope(self, ctx: TenantContext, party: UUID, permission: str) -> None:
        CatalogMaintenance(self.uow).scope(ctx, "party", party, permission)

    def list(self, ctx: TenantContext, party: UUID) -> dict[str, Any]:
        self.scope(ctx, party, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            parent = CatalogMaintenance.record(repo, "party", party)
            rows = repo.rows(
                "SELECT * FROM med.party_certificate WHERE party_id=:id "
                "ORDER BY created_at,id LIMIT 100",
                {"id": party},
            )
            active_rows = rows
            active_parent = parent
            rows = certificate_rows(repo, "party", party, rows)
            parent = editable(repo, "party", parent)
            day = today(repo)
            return {
                "items": [present(r, parent, day) for r in rows],
                "active_items": [present(r, active_parent, day) for r in active_rows],
                "types": TYPES,
                "business_uses": {
                    k: v for k, v in USES.items() if compatibility(parent, [k]) == "MATCH"
                },
                "legacy": jsonable_encoder(
                    {
                        "type": "LEGACY_UNCLASSIFIED",
                        "number": parent["license_no"],
                        "raw_valid_from": parent["valid_from"],
                        "raw_valid_until": parent["valid_until"],
                        "source_version": str(parent["version"]),
                    }
                ),
            }

    def detail(
        self, ctx: TenantContext, party: UUID, certificate: UUID, offset: int = 0
    ) -> dict[str, Any]:
        self.scope(ctx, party, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            parent = CatalogMaintenance.record(repo, "party", party)
            row = record(repo, party, certificate)
            history = repo.rows(
                """SELECT id,created_by,created_at,action,reason,count(*) OVER() AS total
                FROM med.audit_event WHERE resource_type='party' AND resource_id=:party
                AND action IN ('catalog.certificate_created','catalog.certificate_revised',
                  'catalog.certificate_reviewed')
                AND reason::jsonb->>'certificate_id'=:certificate
                ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset""",
                {"party": party, "certificate": str(certificate), "offset": offset},
            )
            return dict(
                jsonable_encoder(
                    {
                        "record": present(row, parent, today(repo)),
                        "history": [
                            {
                                **{k: r[k] for k in ("id", "created_by", "created_at", "action")},
                                "detail": json.loads(r["reason"]),
                            }
                            for r in history
                        ],
                        "has_more": bool(history and offset + len(history) < history[0]["total"]),
                    }
                )
            )

    @staticmethod
    def check_business_uses(parent: dict[str, Any], uses: Sequence[str]) -> None:
        if compatibility(parent, uses) == "MISMATCH":
            raise BusinessError(
                "CERTIFICATE_USE_MISMATCH", "适用业务与当前往来单位供销身份不符", 422
            )

    @staticmethod
    def check_duplicate(
        repo: SqlSession, party: UUID, kind: str, number: str, target: UUID
    ) -> None:
        if repo.rows(
            "SELECT id FROM med.party_certificate WHERE party_id=:party "
            "AND certificate_type=:kind AND lower(btrim(number))=lower(btrim(:number)) AND id<>:id",
            {"party": party, "kind": kind, "number": number, "id": target},
        ):
            raise BusinessError(
                "CERTIFICATE_EXISTS", "此往来单位已有同类型同编号证照，请修订原记录", 409
            )

    def save(
        self,
        ctx: TenantContext,
        party: UUID,
        body: PartyCertificateCreate | PartyCertificateRevision,
        key: str,
        certificate: UUID | None = None,
    ) -> dict[str, Any]:
        self.scope(ctx, party, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            # Serialize certificate writes with classification changes, before locking the child.
            parent = CatalogMaintenance.record(repo, "party", party, lock=True)
            active_parent = parent
            parent = editable(repo, "party", parent)
            self.check_business_uses(parent, body.business_uses)
            if active_parent.get(
                "state", active_parent.get("approval_state")
            ) == "APPROVED" or pending(repo, "party", party):
                if body.attachment_id:
                    attachment_record(repo, "party", party, body.attachment_id)
                target = certificate or uuid4()
                old = record(repo, party, certificate) if certificate else None
                if old:
                    assert isinstance(body, PartyCertificateRevision)
                if old and str(old["version"]) != getattr(body, "expected_version", None):
                    raise BusinessError("VERSION_CONFLICT", "证件或变更草稿已变化，请刷新", 409)
                values = {k: getattr(body, k) for k in PARTY_CERTIFICATE_FIELDS}
                if old and "attachment_id" not in body.model_fields_set:
                    values["attachment_id"] = old["attachment_id"]
                if (
                    old
                    and old["state"] != "INACTIVE"
                    and values == {k: old[k] for k in PARTY_CERTIFICATE_FIELDS}
                ):
                    raise BusinessError("CERTIFICATE_UNCHANGED", "证件内容没有变化", 422)
                self.check_duplicate(repo, party, body.certificate_type, body.number, target)
                rows = certificate_rows(
                    repo,
                    "party",
                    party,
                    repo.rows(
                        "SELECT * FROM med.party_certificate WHERE party_id=:p", {"p": party}
                    ),
                )
                if len(rows) >= 100 and not old:
                    raise BusinessError("CERTIFICATE_LIMIT", "每份主体资料最多维护100份证件", 422)
                if any(
                    r["id"] != target
                    and r["certificate_type"] == body.certificate_type
                    and r["number"].strip().lower() == body.number.strip().lower()
                    for r in rows
                ):
                    raise BusinessError("CERTIFICATE_EXISTS", "变更草稿中已有同类型同编号证件", 409)
                saved = stage_certificate(
                    repo, ctx, "party", active_parent, target, values, old, body.reason
                )
                return present(saved, parent, today(repo))
            if body.attachment_id:
                attachment_record(repo, "party", party, body.attachment_id)
            target = certificate or uuid4()
            values = {k: getattr(body, k) for k in PARTY_CERTIFICATE_FIELDS}
            before = None
            editors = {ctx.principal_id}
            if certificate:
                assert isinstance(body, PartyCertificateRevision)
                before = record(repo, party, certificate, lock=True)
                if str(before["version"]) != body.expected_version:
                    raise BusinessError("VERSION_CONFLICT", "证照已变化，请重新加载后修订", 409)
                if "attachment_id" not in body.model_fields_set:
                    values["attachment_id"] = before["attachment_id"]
                if all(before[k] == values[k] for k in PARTY_CERTIFICATE_FIELDS):
                    raise BusinessError("CERTIFICATE_UNCHANGED", "证照内容没有变化", 422)
                if before["state"] != "VERIFIED":
                    editors.update(before["revision_editors"])
            elif (
                repo.rows(
                    "SELECT count(*) AS count FROM med.party_certificate WHERE party_id=:id",
                    {"id": party},
                )[0]["count"]
                >= 100
            ):
                raise BusinessError("CERTIFICATE_LIMIT", "每个往来单位最多维护100份证照记录", 422)
            self.check_duplicate(repo, party, body.certificate_type, body.number, target)
            params = {
                **values,
                "t": ctx.tenant_id,
                "id": target,
                "party": party,
                "actor": ctx.principal_id,
                "editors": sorted(editors),
            }
            if before:
                repo.execute(
                    f"""UPDATE med.party_certificate SET
                    {",".join(f"{k}=:{k}" for k in PARTY_CERTIFICATE_FIELDS)},state='DRAFT',
                    version=version+1,revision_editors=:editors,reviewed_by=NULL,reviewed_at=NULL,
                    updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                    params,
                )
            else:
                repo.execute(
                    f"""INSERT INTO med.party_certificate
                    (tenant_id,id,party_id,created_by,updated_by,revision_editors,
                      {",".join(PARTY_CERTIFICATE_FIELDS)})
                    VALUES(:t,:id,:party,:actor,:actor,:editors,
                      {",".join(":" + k for k in PARTY_CERTIFICATE_FIELDS)})""",
                    params,
                )
            repo.execute(
                (
                    "UPDATE med.party SET version=version+1,revision_ed"
                    "itors=array_append(revision_editors,:a) WHERE id=:p"
                ),
                {"p": party, "a": ctx.principal_id},
            )
            after = record(repo, party, target)
            audit(
                repo,
                ctx,
                "party",
                party,
                "catalog.certificate_revised" if before else "catalog.certificate_created",
                {
                    "certificate_domain": "party",
                    "certificate_id": str(target),
                    "version": str(after["version"]),
                    "before": snapshot(before) if before else {},
                    "after": snapshot(after),
                    "reason": body.reason,
                    "party_version": str(parent["version"]),
                },
            )
            return present(after, parent, today(repo))

        return command(
            self.uow,
            ctx,
            "catalog.party_certificate.revise"
            if certificate
            else "catalog.party_certificate.create",
            f"{party}:{certificate or 'new'}",
            key,
            body.model_dump(
                mode="json",
                exclude={"attachment_id"}
                if "attachment_id" not in body.model_fields_set
                else set(),
            ),
            run,
        )

    def review(
        self,
        ctx: TenantContext,
        party: UUID,
        certificate: UUID,
        body: PartyCertificateReview,
        key: str,
    ) -> dict[str, Any]:
        self.scope(ctx, party, "catalog.write")
        if body.decision == "VERIFY":
            raise BusinessError(
                "REVIEW_WITH_PARENT", "证件随主体资料一次审核，请使用商品或往来单位的审核入口", 409
            )

        def run(repo: SqlSession) -> dict[str, Any]:
            parent = CatalogMaintenance.record(repo, "party", party, lock=True)
            row = record(repo, party, certificate)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "证件已变化，请刷新", 409)
            values = {k: row[k] for k in PARTY_CERTIFICATE_FIELDS}
            saved = stage_certificate(
                repo, ctx, "party", parent, certificate, values, row, body.reason, "INACTIVE"
            )
            return present(saved, parent, today(repo))

        return command(
            self.uow,
            ctx,
            "catalog.party_certificate.review",
            f"{party}:{certificate}",
            key,
            body.model_dump(mode="json"),
            run,
        )
