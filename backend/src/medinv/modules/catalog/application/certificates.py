import json
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.certificates import (
    CERTIFICATE_FIELDS,
    CertificateCreate,
    CertificateReview,
    CertificateRevision,
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
    "DRUG_APPROVAL": "药品批准证明",
    "DEVICE_REGISTRATION": "医疗器械注册证",
    "DEVICE_FILING": "第一类医疗器械产品备案",
    "HEALTH_REGISTRATION": "保健食品注册",
    "HEALTH_FILING": "保健食品备案",
    "FSMP_REGISTRATION": "特殊医学用途配方食品注册",
    "INFANT_FORMULA_REGISTRATION": "婴幼儿配方乳粉产品配方注册",
    "COSMETIC_FILING": "普通化妆品备案",
    "COSMETIC_REGISTRATION": "特殊化妆品注册",
}


def compatibility(product: dict[str, Any], kind: str) -> str:
    if product["category"] in {"DRUG", "HERB"}:
        return "MATCH" if kind == "DRUG_APPROVAL" else "MISMATCH"
    if product["category"] != "DEVICE":
        allowed = {
            "HEALTH": {"HEALTH_REGISTRATION", "HEALTH_FILING"},
            "FSMP": {"FSMP_REGISTRATION"},
            "INFANT": {"INFANT_FORMULA_REGISTRATION"},
            "COSMETIC": {"COSMETIC_FILING", "COSMETIC_REGISTRATION"},
        }
        return "MATCH" if kind in allowed.get(product["category"], set()) else "MISMATCH"
    if kind == "DRUG_APPROVAL":
        return "MISMATCH"
    if product["device_class"] == "UNKNOWN":
        return "UNCONFIRMED"
    expected = "DEVICE_FILING" if product["device_class"] == "I" else "DEVICE_REGISTRATION"
    return "MATCH" if kind == expected else "MISMATCH"


def snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return dict(jsonable_encoder({k: row[k] for k in (*CERTIFICATE_FIELDS, "state")}))


def today(repo: SqlSession) -> date:
    return cast(
        date,
        repo.rows("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS day", {})[0][
            "day"
        ],
    )


def present(row: dict[str, Any], product: dict[str, Any], day: date) -> dict[str, Any]:
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
                "compatibility": compatibility(product, row["certificate_type"]),
            }
        )
    )


def record(
    repo: SqlSession, product: UUID, certificate: UUID, lock: bool = False
) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.product_certificate WHERE product_id=:product AND id=:id"
        + (" FOR UPDATE" if lock else ""),
        {"product": product, "id": certificate},
    )
    if not lock:
        rows = [
            r for r in certificate_rows(repo, "product", product, rows) if r["id"] == certificate
        ]
    if not rows:
        raise BusinessError("NOT_FOUND", "证照不可见或不属于此商品", 404)
    return rows[0]


class ProductCertificates:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def scope(self, ctx: TenantContext, product: UUID, permission: str) -> None:
        CatalogMaintenance(self.uow).scope(ctx, "product", product, permission)

    def list(self, ctx: TenantContext, product: UUID) -> dict[str, Any]:
        self.scope(ctx, product, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            parent = CatalogMaintenance.record(repo, "product", product)
            rows = repo.rows(
                "SELECT * FROM med.product_certificate WHERE product_id=:id "
                "ORDER BY created_at,id LIMIT 100",
                {"id": product},
            )
            rows = certificate_rows(repo, "product", product, rows)
            parent = editable(repo, "product", parent)
            day = today(repo)
            from medinv.modules.catalog.application.qualification_rules import settings

            type_check = settings(repo)["product_certificate_type_check"]
            return {
                "items": [present(r, parent, day) for r in rows],
                "types": {
                    k: v
                    for k, v in TYPES.items()
                    if not type_check or compatibility(parent, k) != "MISMATCH"
                },
                "legacy": jsonable_encoder(
                    {
                        "type": "LEGACY_UNCLASSIFIED",
                        "number": parent["approval_number"],
                        "raw_valid_from": parent["valid_from"],
                        "raw_valid_until": parent["valid_until"],
                        "source_version": str(parent["version"]),
                    }
                ),
            }

    def detail(
        self, ctx: TenantContext, product: UUID, certificate: UUID, offset: int = 0
    ) -> dict[str, Any]:
        self.scope(ctx, product, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            parent = CatalogMaintenance.record(repo, "product", product)
            row = record(repo, product, certificate)
            history = repo.rows(
                """SELECT id,created_by,created_at,action,reason,count(*) OVER() AS total
                FROM med.audit_event WHERE resource_type='product' AND resource_id=:product
                AND action IN ('catalog.certificate_created','catalog.certificate_revised',
                  'catalog.certificate_reviewed')
                AND reason::jsonb->>'certificate_id'=:certificate
                ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset""",
                {"product": product, "certificate": str(certificate), "offset": offset},
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
    def check_type(parent: dict[str, Any], kind: str) -> None:
        if compatibility(parent, kind) == "MISMATCH":
            raise BusinessError(
                "CERTIFICATE_TYPE_MISMATCH", "证照类型与商品类别或器械分类不符", 422
            )

    @staticmethod
    def check_duplicate(
        repo: SqlSession, product: UUID, kind: str, number: str, target: UUID
    ) -> None:
        if repo.rows(
            "SELECT id FROM med.product_certificate WHERE product_id=:product "
            "AND certificate_type=:kind AND number=:number AND id<>:id",
            {"product": product, "kind": kind, "number": number, "id": target},
        ):
            raise BusinessError(
                "CERTIFICATE_EXISTS", "此商品已有同类型同编号证照，请修订原记录", 409
            )

    def save(
        self,
        ctx: TenantContext,
        product: UUID,
        body: CertificateCreate | CertificateRevision,
        key: str,
        certificate: UUID | None = None,
    ) -> dict[str, Any]:
        self.scope(ctx, product, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            # Serialize certificate writes with classification changes, before locking the child.
            parent = CatalogMaintenance.record(repo, "product", product, lock=True)
            active_parent = parent
            parent = editable(repo, "product", parent)
            from medinv.modules.catalog.application.qualification_rules import settings

            if settings(repo)["product_certificate_type_check"]:
                self.check_type(parent, body.certificate_type)
            if active_parent.get(
                "state", active_parent.get("approval_state")
            ) == "APPROVED" or pending(repo, "product", product):
                if body.attachment_id:
                    attachment_record(repo, "product", product, body.attachment_id)
                target = certificate or uuid4()
                old = record(repo, product, certificate) if certificate else None
                if old:
                    assert isinstance(body, CertificateRevision)
                if old and str(old["version"]) != getattr(body, "expected_version", None):
                    raise BusinessError("VERSION_CONFLICT", "证件或变更草稿已变化，请刷新", 409)
                values = {k: getattr(body, k) for k in CERTIFICATE_FIELDS}
                if old and "attachment_id" not in body.model_fields_set:
                    values["attachment_id"] = old["attachment_id"]
                if (
                    old
                    and old["state"] != "INACTIVE"
                    and values == {k: old[k] for k in CERTIFICATE_FIELDS}
                ):
                    raise BusinessError("CERTIFICATE_UNCHANGED", "证件内容没有变化", 422)
                self.check_duplicate(repo, product, body.certificate_type, body.number, target)
                rows = certificate_rows(
                    repo,
                    "product",
                    product,
                    repo.rows(
                        "SELECT * FROM med.product_certificate WHERE product_id=:p", {"p": product}
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
                    repo, ctx, "product", active_parent, target, values, old, body.reason
                )
                return present(saved, parent, today(repo))
            if body.attachment_id:
                attachment_record(repo, "product", product, body.attachment_id)
            target = certificate or uuid4()
            values = {k: getattr(body, k) for k in CERTIFICATE_FIELDS}
            before = None
            editors = {ctx.principal_id}
            if certificate:
                assert isinstance(body, CertificateRevision)
                before = record(repo, product, certificate, lock=True)
                if str(before["version"]) != body.expected_version:
                    raise BusinessError("VERSION_CONFLICT", "证照已变化，请重新加载后修订", 409)
                if "attachment_id" not in body.model_fields_set:
                    values["attachment_id"] = before["attachment_id"]
                if all(before[k] == values[k] for k in CERTIFICATE_FIELDS):
                    raise BusinessError("CERTIFICATE_UNCHANGED", "证照内容没有变化", 422)
                if before["state"] != "VERIFIED":
                    editors.update(before["revision_editors"])
            elif (
                repo.rows(
                    "SELECT count(*) AS count FROM med.product_certificate WHERE product_id=:id",
                    {"id": product},
                )[0]["count"]
                >= 100
            ):
                raise BusinessError("CERTIFICATE_LIMIT", "每个商品最多维护100份证照记录", 422)
            self.check_duplicate(repo, product, body.certificate_type, body.number, target)
            params = {
                **values,
                "t": ctx.tenant_id,
                "id": target,
                "product": product,
                "actor": ctx.principal_id,
                "editors": sorted(editors),
            }
            if before:
                repo.execute(
                    f"""UPDATE med.product_certificate SET
                    {",".join(f"{k}=:{k}" for k in CERTIFICATE_FIELDS)},state='DRAFT',
                    version=version+1,revision_editors=:editors,reviewed_by=NULL,reviewed_at=NULL,
                    updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                    params,
                )
            else:
                repo.execute(
                    f"""INSERT INTO med.product_certificate
                    (tenant_id,id,product_id,created_by,updated_by,revision_editors,
                      {",".join(CERTIFICATE_FIELDS)})
                    VALUES(:t,:id,:product,:actor,:actor,:editors,
                      {",".join(":" + k for k in CERTIFICATE_FIELDS)})""",
                    params,
                )
            repo.execute(
                (
                    "UPDATE med.product SET version=version+1,revision_e"
                    "ditors=array_append(revision_editors,:a) WHERE id=:p"
                ),
                {"p": product, "a": ctx.principal_id},
            )
            after = record(repo, product, target)
            audit(
                repo,
                ctx,
                "product",
                product,
                "catalog.certificate_revised" if before else "catalog.certificate_created",
                {
                    "certificate_id": str(target),
                    "version": str(after["version"]),
                    "before": snapshot(before) if before else {},
                    "after": snapshot(after),
                    "reason": body.reason,
                    "product_version": str(parent["version"]),
                },
            )
            return present(after, parent, today(repo))

        return command(
            self.uow,
            ctx,
            "catalog.certificate.revise" if certificate else "catalog.certificate.create",
            f"{product}:{certificate or 'new'}",
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
        product: UUID,
        certificate: UUID,
        body: CertificateReview,
        key: str,
    ) -> dict[str, Any]:
        self.scope(ctx, product, "catalog.write")
        if body.decision == "VERIFY":
            raise BusinessError(
                "REVIEW_WITH_PARENT", "证件随主体资料一次审核，请使用商品或往来单位的审核入口", 409
            )

        def run(repo: SqlSession) -> dict[str, Any]:
            parent = CatalogMaintenance.record(repo, "product", product, lock=True)
            row = record(repo, product, certificate)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "证件已变化，请刷新", 409)
            values = {k: row[k] for k in CERTIFICATE_FIELDS}
            saved = stage_certificate(
                repo, ctx, "product", parent, certificate, values, row, body.reason, "INACTIVE"
            )
            return present(saved, parent, today(repo))

        return command(
            self.uow,
            ctx,
            "catalog.certificate.review",
            f"{product}:{certificate}",
            key,
            body.model_dump(mode="json"),
            run,
        )
