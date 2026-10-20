from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from medinv.modules.procurement.api.schemas import OrderCreate
from medinv.modules.procurement.application.service import (
    ProcurementService,
    encode,
    one,
    version,
)
from medinv.modules.sales.api.schemas import ReplenishmentCreate
from medinv.modules.sales.application.stock import SaleStockQueries
from medinv.platform.database.commands import command
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class ReplenishmentService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.procurement = ProcurementService(uow)

    @staticmethod
    def order(repo: SqlSession, ctx: TenantContext, oid: UUID) -> dict[str, Any]:
        ctx.require("sales.read")
        ctx.require("purchase.read")
        row = one(repo, "SELECT * FROM med.sales_order WHERE id=:id", {"id": oid})
        ctx.require("sales.read", row["entity_id"])
        ctx.require("purchase.read", row["entity_id"])
        return row

    def snapshot(self, repo: SqlSession, order: dict[str, Any]) -> dict[str, Any]:
        rows = repo.rows(
            """SELECT l.sku_id,s.code,p.name,s.base_unit,s.qty_scale,
            sum(CASE WHEN :active THEN greatest(d.requested_qty-d.reserved_qty-d.fulfilled_qty,0)
                ELSE 0 END) AS remaining_qty
            FROM med.sales_order_line l JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
            JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
            LEFT JOIN med.inventory_demand_line d ON (d.tenant_id,d.id)=(l.tenant_id,l.id)
            WHERE l.order_id=:id GROUP BY l.sku_id,s.code,p.name,s.base_unit,s.qty_scale
            ORDER BY min(l.line_no),l.sku_id""",
            {"id": order["id"], "active": order["state"] == "APPROVED"},
        )
        stock = SaleStockQueries.snapshot(
            repo,
            "wholesale",
            order["entity_id"],
            order["warehouse_id"],
            [r["sku_id"] for r in rows],
            order["minimum_shelf_days"],
        )
        available = {r["sku_id"]: r for r in stock["items"]}
        pending = {
            r["sku_id"]: r["qty"]
            for r in repo.rows(
                """SELECT l.sku_id,sum(l.qty-l.received_qty-l.closed_qty+coalesce((
                  SELECT sum(rl.qty-rl.posted_qty-rl.rejected_qty) FROM med.receipt_line rl
                  WHERE (rl.tenant_id,rl.order_line_id)=(l.tenant_id,l.id)),0)) AS qty
                FROM med.purchase_order o JOIN med.purchase_order_line l
                  ON (l.tenant_id,l.order_id)=(o.tenant_id,o.id)
                WHERE o.source_sales_order_id=:id AND o.warehouse_id=:w
                  AND o.approval_state IN ('DRAFT','APPROVED') GROUP BY l.sku_id""",
                {"id": order["id"], "w": order["warehouse_id"]},
            )
        }
        purchases = repo.rows(
            """SELECT o.id,o.number,o.entity_id,o.approval_state,o.fulfillment_state,
            o.source_sales_version,o.created_at,p.name AS supplier_name,w.name AS warehouse_name
            FROM med.purchase_order o JOIN med.party p
              ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
            JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
            WHERE o.source_sales_order_id=:id ORDER BY o.created_at DESC,o.id LIMIT 201""",
            {"id": order["id"]},
        )
        if len(purchases) > 200:
            raise BusinessError("RESULT_TOO_LARGE", "关联采购超过当前详情上限", 422)
        with localcontext() as precision:
            precision.prec = 50
            for row in rows:
                item = available[str(row["sku_id"])]
                row["sale_eligible"] = item["sale_eligible"]
                row["available_qty"] = Decimal(item["available_qty"])
                row["shortage_qty"] = max(row["remaining_qty"] - row["available_qty"], Decimal(0))
                row["pending_purchase_qty"] = pending.get(row["sku_id"], Decimal(0))
                row["suggested_qty"] = (
                    max(row["shortage_qty"] - row["pending_purchase_qty"], Decimal(0))
                    if item["sale_eligible"]
                    else Decimal(0)
                )
        return dict(
            encode(
                {
                    "order": {
                        **{
                            k: order[k]
                            for k in (
                                "id",
                                "number",
                                "entity_id",
                                "warehouse_id",
                                "state",
                                "minimum_shelf_days",
                            )
                        },
                        "version": str(order["version"]),
                    },
                    "items": rows,
                    "purchases": purchases,
                    "data_as_of": stock["data_as_of"],
                }
            )
        )

    def detail(self, ctx: TenantContext, oid: UUID) -> dict[str, Any]:
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            return self.snapshot(repo, self.order(repo, ctx, oid))

    def create(
        self, ctx: TenantContext, oid: UUID, body: ReplenishmentCreate, key: str
    ) -> dict[str, Any]:
        ctx.require("purchase.write")
        with self.uow.open(ctx, readonly=True) as session:
            hint = self.order(SqlSession(session), ctx, oid)
        ctx.require("purchase.write", hint["entity_id"])

        def execute(repo: SqlSession) -> dict[str, Any]:
            # Same entity-first order as procurement, sales fulfillment and amendments.
            self.procurement.entity(repo, ctx, hint["entity_id"])
            order = self.order(repo, ctx, oid)
            version(order, body.expected_version)
            if order["state"] != "APPROVED":
                raise BusinessError("INVALID_STATE", "仅已审核且未关闭的销售单可生成补货采购", 409)
            current = self.snapshot(repo, order)
            limits = {UUID(r["sku_id"]): Decimal(r["suggested_qty"]) for r in current["items"]}
            if any(
                line.sku_id not in limits or Decimal(line.qty) > limits[line.sku_id]
                for line in body.lines
            ):
                raise BusinessError(
                    "REPLENISHMENT_CHANGED", "补货需求已变化，请刷新后按最新建议数量核对", 409
                )
            return self.procurement.create_in_transaction(
                repo,
                ctx,
                OrderCreate(
                    entity_id=order["entity_id"],
                    warehouse_id=order["warehouse_id"],
                    supplier_id=body.supplier_id,
                    lines=body.lines,
                ),
                source=order,
            )

        return command(
            self.uow,
            ctx,
            "sales.replenishment",
            str(oid),
            key,
            body.model_dump(mode="json"),
            execute,
        )
