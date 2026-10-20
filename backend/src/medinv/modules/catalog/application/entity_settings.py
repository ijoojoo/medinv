from typing import Any
from uuid import UUID

from medinv.modules.catalog.api.master import EntitySettingsSave
from medinv.modules.catalog.application.dictionaries import validate_dictionary
from medinv.modules.catalog.application.master import raw_record
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

FIELDS = (
    "enabled",
    "purchase_enabled",
    "sales_enabled",
    "recommendation_level",
    "abc_class",
    "abc_secondary_class",
    "business_note",
)


def entity_product_allowed(repo: SqlSession, entity: UUID, ids: list[UUID], channel: str) -> None:
    column = "purchase_enabled" if channel == "purchase" else "sales_enabled"
    rows = repo.rows(
        f"""SELECT x.product_id FROM med.product_entity_settings x JOIN med.sku s
        ON (s.tenant_id,s.product_id)=(x.tenant_id,x.product_id)
        WHERE s.id=ANY(:ids) AND x.entity_id=:e AND (NOT x.enabled OR NOT x.{column})
        FOR SHARE OF x""",
        {"ids": ids, "e": entity},
    )
    if rows:
        raise BusinessError(
            "ENTITY_PRODUCT_DISABLED", "商品在当前经营主体已停用或未开放此购销方向", 422
        )


class ProductEntitySettings:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, product: UUID, entity: UUID) -> dict[str, Any]:
        ctx.require("catalog.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = raw_record(repo, product)
            records = repo.rows(
                "SELECT * FROM med.product_entity_settings WHERE product_id=:p AND entity_id=:e",
                {"p": product, "e": entity},
            )
            source = (
                records[0]
                if records
                else {**row, "enabled": True, "purchase_enabled": True, "sales_enabled": True}
            )
            return {
                "version": records[0]["version"] if records else 0,
                "values": {k: source[k] for k in FIELDS},
                "inherited": not records,
                "entity_id": str(entity),
            }

    def save(
        self, ctx: TenantContext, product: UUID, body: EntitySettingsSave, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            raw_record(repo, product, lock=True)
            if not repo.rows(
                "SELECT id FROM med.legal_entity WHERE id=:e AND status='ACTIVE' FOR SHARE",
                {"e": body.entity_id},
            ):
                raise BusinessError("ENTITY_UNAVAILABLE", "经营主体已停用或不可见", 404)
            current = repo.rows(
                "SELECT * FROM med.product_entity_settings "
                "WHERE product_id=:p AND entity_id=:e FOR UPDATE",
                {"p": product, "e": body.entity_id},
            )
            if (current[0]["version"] if current else 0) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "当前经营主体设置已变化，请重新加载", 409)
            for k in ("recommendation_level", "abc_class", "abc_secondary_class"):
                validate_dictionary(
                    repo, ctx, "abc_class" if k.startswith("abc_") else k, getattr(body, k)
                )
            values = {k: getattr(body, k) for k in FIELDS}
            repo.execute(
                "INSERT INTO med.product_entity_settings(tenant_id,product_id,entity_id,updated_by,"
                + ",".join(FIELDS)
                + ") VALUES(:t,:p,:entity_id,:actor,"
                + ",".join(":" + k for k in FIELDS)
                + ") ON CONFLICT(tenant_id,product_id,entity_id) DO UPDATE SET "
                + ",".join(f"{k}=EXCLUDED.{k}" for k in FIELDS)
                + ",version=product_entity_settings.version+1,updated_by=:actor,updated_at=now()",
                {
                    **values,
                    "t": ctx.tenant_id,
                    "p": product,
                    "entity_id": body.entity_id,
                    "actor": ctx.principal_id,
                },
            )
            audit(
                repo,
                ctx,
                "product",
                product,
                "master.entity_settings.saved",
                {
                    "entity_id": str(body.entity_id),
                    "before": {k: current[0][k] for k in FIELDS} if current else {},
                    "after": values,
                    "reason": body.reason,
                },
            )
            return {"version": body.expected_version + 1}

        return command(
            self.uow,
            ctx,
            "master.entity_settings.save",
            f"{product}:{body.entity_id}",
            key,
            body.model_dump(mode="json"),
            run,
        )
