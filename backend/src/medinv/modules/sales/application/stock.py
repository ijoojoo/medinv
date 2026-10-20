from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any, Literal
from uuid import UUID

from medinv.modules.inventory.application.service import BALANCES
from medinv.modules.procurement.application.service import ZONE, one
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SaleStockQueries:
    """Advisory stock snapshot; only the existing reservation command promises stock."""

    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def lookup(
        self,
        ctx: TenantContext,
        channel: Literal["wholesale", "retail"],
        entity: UUID,
        warehouse: UUID,
        skus: list[UUID],
        minimum_shelf_days: int = 0,
    ) -> dict[str, Any]:
        ctx.require("retail.read" if channel == "retail" else "sales.read", entity)
        if not 1 <= len(skus) <= 50 or not 0 <= minimum_shelf_days <= 3650:
            raise BusinessError("INVALID_QUERY", "每次查询1至50个商品，最低效期0至3650天", 422)
        with self.uow.open(ctx, readonly=True) as session:
            return self.snapshot(
                SqlSession(session), channel, entity, warehouse, skus, minimum_shelf_days
            )

    @staticmethod
    def snapshot(
        repo: SqlSession,
        channel: Literal["wholesale", "retail"],
        entity: UUID,
        warehouse: UUID,
        skus: list[UUID],
        minimum_shelf_days: int = 0,
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(skus))
        one(
            repo,
            """SELECT w.id FROM med.warehouse w JOIN med.legal_entity e
            ON (e.tenant_id,e.id)=(w.tenant_id,w.entity_id)
            WHERE w.id=:w AND w.entity_id=:e AND w.kind='PHYSICAL'
            AND w.archived_at IS NULL AND e.status='ACTIVE'""",
            {"w": warehouse, "e": entity},
        )
        now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
        products = repo.rows(
            """SELECT s.id,s.code,s.base_unit,p.name,
            coalesce((p.approval_state='APPROVED' AND p.restricted_category IS NULL
             AND med.product_qualification_current(p)
             AND s.lot_required AND NOT s.serial_required
             AND (NOT :retail OR g.id IS NOT NULL) AND NOT coalesce(g.blocked,false)
             AND (NOT :retail OR p.category='DEVICE' OR p.drug_sale_class='OTC')),false)
              AS sale_eligible
            FROM med.sku s JOIN med.product p
            ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
            LEFT JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id)
              AND g.entity_id=:entity
            WHERE s.id=ANY(:skus)""",
            {
                "entity": entity,
                "skus": ids,
                "day": now.astimezone(ZONE).date(),
                "retail": channel == "retail",
            },
        )
        if len(products) != len(ids):
            raise BusinessError("SKU_NOT_FOUND", "商品不可见或不存在", 404)
        records = repo.rows(
            BALANCES + " ORDER BY b.id LIMIT 2001",
            {"entity": entity, "warehouse": warehouse, "skus": ids},
        )
        if len(records) > 2000:
            raise BusinessError("ALLOCATION_TOO_LARGE", "候选库存过多，请减少商品后查询", 422)
        by_sku = {row["id"]: row for row in products}
        available = dict.fromkeys(ids, Decimal(0))
        minimum = now + timedelta(days=minimum_shelf_days)
        with localcontext() as decimal_ctx:
            decimal_ctx.prec = 50
            for row in records:
                if (
                    by_sku[row["sku_id"]]["sale_eligible"]
                    and not row["ineligibility_codes"]
                    and (
                        channel == "retail"
                        or (
                            row["expiry_exclusive"] is not None
                            and row["expiry_exclusive"] >= minimum
                        )
                    )
                ):
                    available[row["sku_id"]] += max(
                        row["on_hand"] - row["reserved"] - row["blocked"], Decimal(0)
                    )
        return {
            "items": [
                {
                    "sku_id": str(sku),
                    **{k: by_sku[sku][k] for k in ("name", "code", "base_unit", "sale_eligible")},
                    "available_qty": format(available[sku], ".6f"),
                }
                for sku in ids
            ],
            "data_as_of": now.isoformat(),
            "warehouse_id": str(warehouse),
            "minimum_shelf_days": minimum_shelf_days if channel == "wholesale" else 0,
        }
