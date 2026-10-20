from datetime import date
from typing import Any
from uuid import UUID

from medinv.modules.catalog.application.master_config import current_schema
from medinv.modules.procurement.application.document_projection import project, public_documents
from medinv.modules.procurement.application.service import encode, one
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SalesQueries:
    uow: TenantUnitOfWork

    def options(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int = 0,
        include_skus: bool = True,
        include_parties: bool = True,
    ) -> dict[str, Any]:
        ctx.require("sales.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            customers = (
                repo.rows(
                    """SELECT id,name FROM med.party WHERE entity_id=:e
                AND kind IN ('CUSTOMER','BOTH') AND state='APPROVED'
                AND (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
                BETWEEN valid_from AND valid_until ORDER BY name,id LIMIT 201 OFFSET :offset""",
                    {"e": entity, "offset": offset},
                )
                if include_parties
                else []
            )

            warehouses = repo.rows(
                """SELECT id,name FROM med.warehouse WHERE entity_id=:e
                AND kind='PHYSICAL'
                AND archived_at IS NULL ORDER BY name,id LIMIT 201 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            skus = (
                repo.rows(
                    """SELECT s.id,p.name,s.code,s.base_unit FROM med.sku s
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                LEFT JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id)
                  AND g.entity_id=:e
                WHERE NOT coalesce(g.blocked,false) AND s.lot_required AND NOT s.serial_required
                AND p.approval_state='APPROVED' AND p.restricted_category IS NULL
                AND med.product_qualification_current(p)
                ORDER BY p.name,s.id LIMIT 201 OFFSET :offset""",
                    {"e": entity, "offset": offset},
                )
                if include_skus
                else []
            )
            more = any(len(items) > 200 for items in (customers, warehouses, skus))
            return dict(
                encode(
                    {
                        "customers": customers[:200],
                        "warehouses": warehouses[:200],
                        "skus": skus[:200],
                        "has_more": more,
                        "next_offset": offset + 200 if more else None,
                    }
                )
            )

    def orders(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int,
        search: str = "",
        state: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        warehouse_id: UUID | None = None,
        party_id: UUID | None = None,
    ) -> dict[str, Any]:
        ctx.require("sales.read", entity)
        filters, params = document_filters(
            "o",
            "concat_ws(' ',o.number,p.name,p.code)",
            {
                s: f"o.state='{s}'"
                for s in ("DRAFT", "APPROVED", "FULFILLED", "CLOSED", "CANCELLED")
            },
            search,
            state,
            date_from,
            date_to,
        )
        if warehouse_id:
            filters += " AND o.warehouse_id=:filter_warehouse"
            params["filter_warehouse"] = warehouse_id
        if party_id:
            filters += " AND o.customer_id=:filter_party"
            params["filter_party"] = party_id
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT o.*,p.name AS customer_name,
                w.name AS warehouse_name,count(*) OVER() AS total FROM med.sales_order o
                JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.customer_id)
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
                WHERE o.entity_id=:e {filters}
                ORDER BY o.created_at DESC,o.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(self, ctx: TenantContext, oid: UUID) -> dict[str, Any]:
        ctx.require("sales.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            order = one(
                repo,
                """SELECT o.*,p.name AS customer_name,w.name AS warehouse_name
                FROM med.sales_order o JOIN med.party p ON
                (p.tenant_id,p.id)=(o.tenant_id,o.customer_id)
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
                WHERE o.id=:id""",
                {"id": oid},
            )
            ctx.require("sales.read", order["entity_id"])
            order["version"] = str(order["version"])
            order["can_revise"] = (
                order["state"] in {"DRAFT", "APPROVED"}
                and not repo.rows(
                    "SELECT id FROM med.reservation WHERE source_id=:id LIMIT 1", {"id": oid}
                )
                and not repo.rows(
                    "SELECT id FROM med.shipment WHERE order_id=:id LIMIT 1", {"id": oid}
                )
            )
            lines = repo.rows(
                """SELECT l.*,p.name,s.base_unit,
                coalesce(d.reserved_qty,0) AS
                reserved_qty,coalesce(d.fulfilled_qty,0) AS fulfilled_qty,
                l.qty-coalesce(d.reserved_qty,0)-coalesce(d.fulfilled_qty,0) AS remaining_qty
                FROM med.sales_order_line l JOIN med.sku s ON
                (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                LEFT JOIN med.inventory_demand_line d ON (d.tenant_id,d.id)=(l.tenant_id,l.id)
                WHERE l.order_id=:id ORDER BY l.line_no""",
                {"id": oid},
            )
            shipments = repo.rows(
                """SELECT sh.*,r.expires_at,r.auto_released_at,
                r.expires_at<=clock_timestamp() AS expired
                FROM med.shipment sh JOIN med.reservation r
                ON (r.tenant_id,r.id)=(sh.tenant_id,sh.reservation_id)
                WHERE sh.order_id=:id ORDER BY sh.created_at,sh.id LIMIT 201""",
                {"id": oid},
            )
            if len(shipments) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "发货子单超过当前详情上限", 422)
            for shipment in shipments:
                shipment["version"] = str(shipment["version"])
                shipment["lines"] = repo.rows(
                    """SELECT sl.*,a.allocated_qty AS qty,b.sku_id,
                    l.lot_no,l.raw_expiry,loc.code AS location_code,p.name,s.base_unit
                    FROM med.shipment_line sl JOIN med.reservation_allocation a
                    ON (a.tenant_id,a.id)=(sl.tenant_id,sl.allocation_id)
                    JOIN med.stock_balance b ON (b.tenant_id,b.id)=(a.tenant_id,a.balance_id)
                    JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                    JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
                    JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
                    JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                    WHERE sl.shipment_id=:id ORDER BY sl.id""",
                    {"id": shipment["id"]},
                )
            schema = current_schema(repo)["payload"]
            for line in lines:
                project(line, line, "sales_orders", schema, ctx.permissions)
            return dict(
                encode(public_documents({"order": order, "lines": lines, "shipments": shipments}))
            )

    def receivables(self, ctx: TenantContext, entity: UUID, offset: int) -> dict[str, Any]:
        ctx.require("sales.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT r.*,i.settled_amount,i.outstanding_amount,i.returned_amount,
                p.name AS customer_name,
                sh.number AS shipment_number,count(*) OVER() AS total,
                (SELECT -sum(cl.amount_delta) FROM med.cost_ledger cl JOIN med.posting_line pl
                 ON (pl.tenant_id,pl.id)=(cl.tenant_id,cl.posting_line_id)
                 WHERE pl.posting_id=r.posting_id) AS cost_amount
                FROM med.receivable_obligation r JOIN med.settlement_item i
                ON (i.tenant_id,i.id)=(r.tenant_id,r.id) AND i.kind='AR' JOIN med.party p
                ON (p.tenant_id,p.id)=(r.tenant_id,r.customer_id)
                JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(r.tenant_id,r.shipment_id)
                WHERE r.entity_id=:e ORDER BY r.created_at DESC,r.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }
