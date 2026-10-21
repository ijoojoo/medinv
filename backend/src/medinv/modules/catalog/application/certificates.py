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
            day = today(repo)
            return {
                "items": [present(r, parent, day) for r in rows],
                "types": {k: v for k, v in TYPES.items() if compatibility(parent, k) != "MISMATCH"},
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
            self.check_type(parent, body.certificate_type)
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
        self.scope(ctx, product, "catalog.review")

        def run(repo: SqlSession) -> dict[str, Any]:
            parent = CatalogMaintenance.record(repo, "product", product, lock=True)
            row = record(repo, product, certificate, lock=True)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "证照已变化，请重新加载后核验", 409)
            if body.decision == "VERIFY":
                if row["state"] != "DRAFT":
                    raise BusinessError("CERTIFICATE_STATE", "仅待核验记录可以核验通过", 409)
                if (
                    ctx.principal_id == row["created_by"]
                    or ctx.principal_id in row["revision_editors"]
                ):
                    raise BusinessError(
                        "SEPARATION_REQUIRED", "建档人及本轮修订人员不能核验通过", 403
                    )
                self.check_type(parent, row["certificate_type"])
                if compatibility(parent, row["certificate_type"]) == "UNCONFIRMED":
                    raise BusinessError("CERTIFICATE_NOT_READY", "请先核定商品的器械分类", 422)
                if not row["holder_name"] or not row["issuer"] or not row["evidence_ref"]:
                    raise BusinessError(
                        "CERTIFICATE_NOT_READY", "请补齐持有人、发证/备案机关和原件凭据", 422
                    )
                if row["validity_mode"] == "UNCONFIRMED" or (
                    row["certificate_type"]
                    not in {"DEVICE_FILING", "HEALTH_FILING", "COSMETIC_FILING"}
                    and row["validity_mode"] != "FIXED"
                ):
                    raise BusinessError(
                        "CERTIFICATE_NOT_READY",
                        "请按原件核实有效期，批准/注册证须填写起止日期",
                        422,
                    )
                state = "VERIFIED"
            else:
                if row["state"] == "INACTIVE":
                    raise BusinessError("CERTIFICATE_STATE", "证照已停用", 409)
                state = "INACTIVE"
            repo.execute(
                """UPDATE med.product_certificate SET state=:state,version=version+1,
                reviewed_by=:actor,reviewed_at=clock_timestamp(),updated_by=:actor,
                updated_at=clock_timestamp(),revision_editors=CASE
                WHEN CAST(:state AS varchar)='VERIFIED'
                THEN ARRAY[]::uuid[] ELSE revision_editors END WHERE id=:id""",
                {"state": state, "actor": ctx.principal_id, "id": certificate},
            )
            after = record(repo, product, certificate)
            audit(
                repo,
                ctx,
                "product",
                product,
                "catalog.certificate_reviewed",
                {
                    **body.model_dump(mode="json"),
                    "certificate_id": str(certificate),
                    "version": str(after["version"]),
                    "before": snapshot(row),
                    "after": snapshot(after),
                    "product_version": str(parent["version"]),
                },
            )
            return present(after, parent, today(repo))

        return command(
            self.uow,
            ctx,
            "catalog.certificate.review",
            f"{product}:{certificate}",
            key,
            body.model_dump(mode="json"),
            run,
        )
