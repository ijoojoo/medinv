import json
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError

from medinv.modules.catalog.api.codes import CatalogCodeCreate, CatalogCodeState, CodeOwner
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def lock_codes(repo: SqlSession, ctx: TenantContext, kind: str, target: UUID) -> None:
    repo.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))",
        {"scope": f"catalog-codes:{ctx.tenant_id}:{kind}:{target}"},
    )


class CatalogCodes:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    @staticmethod
    def target(
        repo: SqlSession, ctx: TenantContext, kind: CodeOwner, target: UUID, permission: str
    ) -> dict[str, Any]:
        ctx.require(permission)
        rows = repo.rows(
            "SELECT s.id,p.category FROM med.sku s JOIN med.product p "
            "ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id) WHERE s.id=:id"
            if kind == "skus"
            else "SELECT id,entity_id FROM med.party WHERE id=:id",
            {"id": target},
        )
        if not rows:
            raise BusinessError("NOT_FOUND", "资料不可见或不存在", 404)
        if kind == "parties":
            ctx.require(permission, rows[0]["entity_id"])
        return rows[0]

    def scope(self, ctx: TenantContext, kind: CodeOwner, target: UUID, permission: str) -> None:
        with self.uow.open(ctx, readonly=True) as session:
            self.target(SqlSession(session), ctx, kind, target, permission)

    @staticmethod
    def validate(
        repo: SqlSession,
        kind: CodeOwner,
        target: UUID,
        owner: dict[str, Any],
        scheme: str,
        packaging: UUID | None,
    ) -> None:
        product_schemes = {"BARCODE", "UDI_DI", "NHSA_DRUG", "NHSA_CONSUMABLE", "NHSA_IVD"}
        invalid = (kind == "parties" and scheme in product_schemes) or (
            kind == "skus"
            and (
                scheme == "NHSA_ORGANIZATION"
                or (scheme == "NHSA_DRUG" and owner["category"] != "DRUG")
                or (
                    scheme in {"UDI_DI", "NHSA_CONSUMABLE", "NHSA_IVD"}
                    and owner["category"] != "DEVICE"
                )
            )
        )
        if invalid:
            raise BusinessError("CODE_CATEGORY_INVALID", "编码类型与商品类别或往来单位不符", 422)
        if packaging and (
            kind != "skus"
            or not repo.rows(
                "SELECT id FROM med.packaging WHERE id=:p AND sku_id=:s "
                "AND state='APPROVED' AND effective_until IS NULL FOR SHARE",
                {"p": packaging, "s": target},
            )
        ):
            raise BusinessError("CODE_PACKAGE_INVALID", "请选择本商品已审核启用的包装单位", 422)

    def list(
        self,
        ctx: TenantContext,
        kind: CodeOwner,
        target: UUID,
        offset: int,
        schemes: list[str] | None = None,
    ) -> dict[str, Any]:
        self.scope(ctx, kind, target, "catalog.read")
        column = "sku_id" if kind == "skus" else "party_id"
        params = {
            "id": target,
            "offset": offset,
            "filtered": bool(schemes),
            "schemes": schemes or [],
        }
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                f"""SELECT c.*,p.unit_name,p.state AS packaging_state,count(*) OVER() AS total
                FROM med.catalog_code c LEFT JOIN med.packaging p
                  ON (p.tenant_id,p.id)=(c.tenant_id,c.packaging_id)
                WHERE c.{column}=:id
                AND (NOT :filtered OR c.scheme=ANY(CAST(:schemes AS varchar[])))
                ORDER BY c.created_at DESC,c.id LIMIT 50 OFFSET :offset""",
                params,
            )
            events = repo.rows(
                f"""SELECT a.id,a.created_at,a.created_by,a.action,a.reason
                FROM med.audit_event a JOIN med.catalog_code c
                  ON (c.tenant_id,c.id)=(a.tenant_id,a.resource_id)
                WHERE a.resource_type='catalog_code' AND c.{column}=:id
                AND (NOT :filtered OR c.scheme=ANY(CAST(:schemes AS varchar[])))
                ORDER BY a.created_at DESC,a.id LIMIT 50""",
                params,
            )
            return dict(
                jsonable_encoder(
                    {
                        "items": [{**r, "version": str(r["version"])} for r in rows],
                        "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
                        "history": [{**e, "detail": json.loads(e["reason"])} for e in events],
                    }
                )
            )

    def create(
        self, ctx: TenantContext, kind: CodeOwner, target: UUID, body: CatalogCodeCreate, key: str
    ) -> dict[str, Any]:
        self.scope(ctx, kind, target, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            lock_codes(repo, ctx, kind, target)
            owner = self.target(repo, ctx, kind, target, "catalog.write")
            self.validate(repo, kind, target, owner, body.scheme, body.packaging_id)
            id = uuid4()
            p = {
                **body.model_dump(),
                "id": id,
                "t": ctx.tenant_id,
                "actor": ctx.principal_id,
                "sku": target if kind == "skus" else None,
                "party": target if kind == "parties" else None,
                "entity": owner.get("entity_id"),
            }
            inserted = repo.rows(
                """INSERT INTO med.catalog_code
                (tenant_id,id,sku_id,party_id,entity_id,packaging_id,scheme,namespace,external_code,
                 evidence_ref,created_by,updated_by)
                VALUES (:t,:id,:sku,:party,:entity,:packaging_id,:scheme,:namespace,:external_code,
                 :evidence_ref,:actor,:actor) ON CONFLICT DO NOTHING RETURNING id""",
                p,
            )
            if not inserted:
                raise BusinessError(
                    "EXTERNAL_CODE_EXISTS", "该范围的编码已有启用映射，请核对原记录", 409
                )
            audit(
                repo,
                ctx,
                "catalog_code",
                id,
                "catalog_code.created",
                {
                    **body.model_dump(mode="json"),
                    "target_kind": kind,
                    "target_id": str(target),
                },
            )
            return {"id": str(id), "version": "1", "state": "ACTIVE"}

        return command(
            self.uow,
            ctx,
            "catalog_code.create",
            f"{kind}:{target}",
            key,
            body.model_dump(mode="json"),
            run,
        )

    def change_state(
        self,
        ctx: TenantContext,
        kind: CodeOwner,
        target: UUID,
        id: UUID,
        body: CatalogCodeState,
        key: str,
    ) -> dict[str, Any]:
        self.scope(ctx, kind, target, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            lock_codes(repo, ctx, kind, target)
            owner = self.target(repo, ctx, kind, target, "catalog.write")
            column = "sku_id" if kind == "skus" else "party_id"
            rows = repo.rows(
                f"SELECT * FROM med.catalog_code WHERE id=:id AND {column}=:target FOR UPDATE",
                {"id": id, "target": target},
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "编码映射不可见或不存在", 404)
            row = rows[0]
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "编码映射已变化，请刷新后操作", 409)
            if row["state"] == body.state:
                raise BusinessError("CODE_UNCHANGED", "编码状态没有变化", 422)
            if body.state == "ACTIVE":
                self.validate(repo, kind, target, owner, row["scheme"], row["packaging_id"])
            repo.execute(
                """UPDATE med.catalog_code SET state=:state,version=version+1,
                updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                {"id": id, "state": body.state, "actor": ctx.principal_id},
            )
            audit(
                repo,
                ctx,
                "catalog_code",
                id,
                "catalog_code.state",
                {
                    **body.model_dump(mode="json"),
                    "previous_state": row["state"],
                    "external_code": row["external_code"],
                    "scheme": row["scheme"],
                },
            )
            return {"id": str(id), "version": str(row["version"] + 1), "state": body.state}

        try:
            return command(
                self.uow,
                ctx,
                "catalog_code.state",
                f"{kind}:{target}:{id}",
                key,
                body.model_dump(mode="json"),
                run,
            )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise BusinessError(
                    "EXTERNAL_CODE_EXISTS", "该范围的编码已有启用映射，不能重复启用", 409
                ) from exc
            raise
