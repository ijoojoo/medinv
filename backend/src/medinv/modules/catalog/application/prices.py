from typing import Any
from uuid import UUID, uuid4

from medinv.modules.catalog.api.prices import ReferencePricesUpdate
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

FIELDS = ("purchase_price", "wholesale_price", "retail_price", "note")


def current(repo: SqlSession, sku: UUID) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT s.product_id,s.base_unit,p.version,p.purchase_price,"
        "p.wholesale_price,p.retail_price,p.note FROM med.sku s "
        "LEFT JOIN med.sku_reference_price p "
        "ON (p.tenant_id,p.sku_id)=(s.tenant_id,s.id) WHERE s.id=:id",
        {"id": sku},
    )
    if not rows:
        raise BusinessError("NOT_FOUND", "商品不可见或不存在", 404)
    row = rows[0]
    return {
        "product_id": str(row["product_id"]),
        "version": str(row["version"] or 0),
        "base_unit": row["base_unit"],
        "values": {
            k: (row[k] or "") if k == "note" else str(row[k]) if row[k] is not None else None
            for k in FIELDS
        },
    }


class ReferencePrices:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, sku: UUID) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            return current(SqlSession(session), sku)

    def update(
        self, ctx: TenantContext, sku: UUID, body: ReferencePricesUpdate, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.write")
        # Check visibility before reading a cached command result.
        self.get(ctx, sku)

        return command(
            self.uow,
            ctx,
            "catalog.prices.update",
            str(sku),
            key,
            body.model_dump(mode="json"),
            lambda repo: self.write(repo, ctx, sku, body),
        )

    @staticmethod
    def write(
        repo: SqlSession,
        ctx: TenantContext,
        sku: UUID,
        body: ReferencePricesUpdate,
        *,
        allow_unchanged: bool = False,
    ) -> dict[str, Any]:
        ctx.require("catalog.write")
        repo.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))",
            {"scope": f"reference-prices:{ctx.tenant_id}:{sku}"},
        )
        before = current(repo, sku)
        if body.expected_version != before["version"]:
            raise BusinessError("VERSION_CONFLICT", "参考价格已变化，请重新加载", 409)
        after = {k: getattr(body, k) for k in FIELDS}
        from decimal import Decimal

        if (
            all(
                (Decimal(before["values"][k]) if before["values"][k] is not None else None)
                == (Decimal(after[k]) if after[k] is not None else None)
                for k in FIELDS[:-1]
            )
            and before["values"]["note"] == after["note"]
        ):
            if allow_unchanged:
                return {"sku_id": str(sku), "version": before["version"]}
            raise BusinessError("PRICES_UNCHANGED", "价格信息没有变化", 422)
        row = repo.rows(
            """INSERT INTO med.sku_reference_price
          (tenant_id,id,sku_id,purchase_price,wholesale_price,retail_price,note,created_by,updated_by)
          VALUES(:t,:id,:sku,:purchase_price,:wholesale_price,:retail_price,:note,:actor,:actor)
          ON CONFLICT(tenant_id,sku_id) DO UPDATE SET purchase_price=:purchase_price,
          wholesale_price=:wholesale_price,retail_price=:retail_price,note=:note,
          version=sku_reference_price.version+1,updated_by=:actor,updated_at=clock_timestamp()
          RETURNING version""",
            {**after, "t": ctx.tenant_id, "id": uuid4(), "sku": sku, "actor": ctx.principal_id},
        )[0]
        audit(
            repo,
            ctx,
            "product",
            UUID(before["product_id"]),
            "catalog.prices_updated",
            {
                "sku_id": str(sku),
                "before": before["values"],
                "after": after,
                "reason": body.reason,
                "version": str(row["version"]),
            },
        )
        return {"sku_id": str(sku), "version": str(row["version"])}
