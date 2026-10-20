import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.catalog.application.packaging import trade
from medinv.modules.catalog.application.profile import product_snapshot
from medinv.modules.inventory.api.schemas import ReleaseRequest, ReservationLine, ReserveRequest
from medinv.modules.inventory.domain.decimal_values import money_for_posting
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.retail.api.schemas import RetailCreate
from medinv.modules.sales.application.service import SalesService
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class RetailService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)
        self.sales = SalesService(uow)
        self.inventory = self.sales.inventory

    def scope(self, ctx: TenantContext, sale_id: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id FROM med.retail_sale WHERE id=:id",
                {"id": sale_id},
            )
        ctx.require(permission, row["entity_id"])
        return UUID(str(row["entity_id"]))

    def products(
        self, repo: SqlSession, entity: UUID, ids: list[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        rows = self.sales.products(
            repo, entity, ids, {"categories": ["DRUG", "DEVICE"]}, require_stock_gate=True
        )
        if any(r["category"] == "DRUG" and r["drug_sale_class"] != "OTC" for r in rows.values()):
            raise BusinessError(
                "RETAIL_RX_NOT_ENABLED", "当前收银仅开放已审核非处方药及普通器械", 409
            )
        return rows

    def options(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("retail.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            return dict(
                encode(
                    {
                        "warehouses": repo.rows(
                            """SELECT id,name FROM med.warehouse
                    WHERE entity_id=:e AND kind='PHYSICAL'
                    AND archived_at IS NULL ORDER BY name,id LIMIT 200""",
                            {"e": entity},
                        ),
                        "accounts": repo.rows(
                            """SELECT id,name FROM med.cash_account
                    WHERE entity_id=:e AND method='CASH' ORDER BY name,id LIMIT 200""",
                            {"e": entity},
                        ),
                    }
                )
            )

    def catalog(self, ctx: TenantContext, entity: UUID, search: str, offset: int) -> dict[str, Any]:
        ctx.require("retail.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """
                SELECT s.id,s.code,s.base_unit,s.qty_scale,p.name,p.specification,p.category,
                  p.drug_sale_class,count(*) OVER() AS total
                FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id)
                WHERE g.entity_id=:e AND NOT g.blocked AND p.approval_state='APPROVED'
                AND p.restricted_category IS NULL AND s.lot_required AND NOT s.serial_required
                AND (p.category='DEVICE' OR p.drug_sale_class='OTC')
                AND med.product_qualification_current(p)
                AND (p.name ILIKE :q OR s.code ILIKE :q)
                ORDER BY p.name,s.id LIMIT 50 OFFSET :offset""",
                {
                    "e": entity,
                    "day": self.finance.today(SqlSession(session)),
                    "q": "%" + search + "%",
                    "offset": offset,
                },
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def create(self, ctx: TenantContext, body: RetailCreate, key: str) -> dict[str, Any]:
        ctx.require("retail.sell", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "retail.sell")
            skus = self.products(repo, body.entity_id, [r.sku_id for r in body.lines])
            one(
                repo,
                """SELECT id FROM med.warehouse WHERE id=:id AND entity_id=:e
                AND kind='PHYSICAL' AND archived_at IS NULL FOR SHARE""",
                {"id": body.warehouse_id, "e": body.entity_id},
            )
            prepared = []
            try:
                for line in body.lines:
                    qty, package = trade(repo, skus[line.sku_id], line.packaging_id, line.qty)
                    gross = money_for_posting(Decimal(line.qty) * Decimal(line.unit_price))
                    net = money_for_posting(gross / (1 + Decimal(line.tax_rate)))
                    prepared.append(
                        {
                            **line.model_dump(),
                            "id": uuid4(),
                            "qty": qty,
                            "gross": gross,
                            "net": net,
                            "trade": Decimal(line.qty),
                            "package": json.dumps(package),
                        }
                    )
                gross = money_for_posting(sum((r["gross"] for r in prepared), Decimal(0)))
                net = money_for_posting(sum((r["net"] for r in prepared), Decimal(0)))
            except ValueError as exc:
                raise BusinessError(
                    "INVALID_AMOUNT", "零售数量精度或金额超出支持范围", 422
                ) from exc
            if gross <= 0:
                raise BusinessError("INVALID_AMOUNT", "现金收银单应收金额必须大于零", 422)
            sid = uuid4()
            number = document_number("POS", sid)
            params = {
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "id": sid,
                "e": body.entity_id,
                "w": body.warehouse_id,
                "n": number,
                "gross": gross,
                "net": net,
            }
            repo.execute(
                """INSERT INTO med.inventory_demand(tenant_id,id,entity_id,warehouse_id,
                source_type,state,checks_completed,eligible_until)
                VALUES(:t,:id,:e,:w,'RETAIL_SALE','APPROVED',true,now()+interval '1 day')""",
                params,
            )
            for row in prepared:
                repo.execute(
                    """INSERT INTO med.inventory_demand_line(tenant_id,id,demand_id,
                    sku_id,requested_qty) VALUES(:t,:line,:id,:sku_id,:qty)""",
                    {**params, **row, "line": row["id"], "id": sid},
                )
            reserve = ReserveRequest(
                client_operation_id=uuid4(),
                entity_id=body.entity_id,
                warehouse_id=body.warehouse_id,
                source_type="RETAIL_SALE",
                source_id=sid,
                expected_source_version="1",
                lines=[
                    ReservationLine(
                        source_line_id=r["id"], sku_id=r["sku_id"], base_qty=format(r["qty"], "f")
                    )
                    for r in prepared
                ],
            )
            reservation = self.inventory._reserve(
                repo, ctx, reserve, fingerprint(reserve.model_dump(mode="json")), retail=True
            )
            repo.execute(
                """INSERT INTO med.retail_sale(tenant_id,id,entity_id,warehouse_id,
                reservation_id,number,gross_amount,net_amount,created_by)
                VALUES(:t,:id,:e,:w,:reservation,:n,:gross,:net,:a)""",
                {**params, "reservation": UUID(reservation["reservation_id"])},
            )
            for index, row in enumerate(prepared, 1):
                sku = skus[row["sku_id"]]
                repo.execute(
                    """INSERT INTO med.retail_sale_line(tenant_id,id,sale_id,line_no,
                    sku_id,qty,unit_price,tax_rate,gross_amount,net_amount,
                    product_snapshot,package_snapshot,trade_qty)
                    VALUES(:t,:line,:id,:index,:sku_id,:qty,:unit_price,:tax_rate,:gross,:net,
                    CAST(:product AS jsonb),CAST(:package AS jsonb),:trade)""",
                    {
                        **params,
                        **row,
                        "line": row["id"],
                        "id": sid,
                        "index": index,
                        "product": json.dumps(product_snapshot(sku)),
                    },
                )
            result = {
                "id": str(sid),
                "number": number,
                "state": "RESERVED",
                "version": "1",
                "gross_amount": format(gross, ".2f"),
                "expires_at": reservation["expires_at"],
            }
            audit(repo, ctx, "retail_sale", sid, "retail.created", body.model_dump(mode="json"))
            return result

        return self.finance.permanent(
            ctx,
            "retail.create",
            str(body.entity_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def listing(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int,
        search: str = "",
        state: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        ctx.require("retail.read", entity)
        filters, params = document_filters(
            "r",
            "r.number",
            {
                s: f"r.state='{s}'"
                for s in ("RESERVED", "PAID_PENDING_POST", "COMPLETED", "CANCELLED", "REFUNDED")
            },
            search,
            state,
            date_from,
            date_to,
        )
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT r.*,rv.auto_released_at,count(*) OVER() AS total,
                w.name AS warehouse_name FROM med.retail_sale r JOIN med.warehouse w
                  ON (w.tenant_id,w.id)=(r.tenant_id,r.warehouse_id)
                JOIN med.reservation rv ON (rv.tenant_id,rv.id)=(r.tenant_id,r.reservation_id)
                WHERE r.entity_id=:e {filters}
                ORDER BY r.created_at DESC,r.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def lines(self, repo: SqlSession, sid: UUID) -> list[dict[str, Any]]:
        return repo.rows(
            "SELECT * FROM med.retail_sale_line WHERE sale_id=:id ORDER BY line_no", {"id": sid}
        )

    def allocations(self, repo: SqlSession, reservation: UUID) -> list[dict[str, Any]]:
        return repo.rows(
            """SELECT a.*,b.sku_id,b.lot_id,b.location_id,b.on_hand,b.reserved,b.blocked,
            b.quality_status,l.lot_no,l.expiry_exclusive,l.quality_blocked
            FROM med.reservation_allocation a JOIN med.stock_balance b
              ON (b.tenant_id,b.id)=(a.tenant_id,a.balance_id)
            JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
            WHERE a.reservation_id=:id ORDER BY a.balance_id,a.id""",
            {"id": reservation},
        )

    def detail(self, ctx: TenantContext, sid: UUID) -> dict[str, Any]:
        self.scope(ctx, sid, "retail.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            sale = one(repo, "SELECT * FROM med.retail_sale WHERE id=:id", {"id": sid})
            sale["version"] = str(sale["version"])
            reservation = one(
                repo, "SELECT * FROM med.reservation WHERE id=:id", {"id": sale["reservation_id"]}
            )
            result = None
            if sale["posting_id"]:
                result = one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": sale["posting_id"]},
                )["result_snapshot"]["response"]
            return_totals = one(
                repo,
                """SELECT coalesce(sum(gross_amount),0) AS returned_amount
                FROM med.return_document WHERE retail_sale_id=:id AND state='POSTED'""",
                {"id": sid},
            )
            refund_totals = one(
                repo,
                """SELECT coalesce(sum(amount),0) AS refunded_amount
                FROM med.retail_return_refund WHERE sale_id=:id""",
                {"id": sid},
            )
            return dict(
                encode(
                    {
                        "sale": sale,
                        "returns_summary": {
                            **return_totals,
                            **refund_totals,
                            "pending_refund_amount": return_totals["returned_amount"]
                            - refund_totals["refunded_amount"],
                        },
                        "lines": self.lines(repo, sid),
                        "reservation": reservation,
                        "allocations": self.allocations(repo, sale["reservation_id"]),
                        "result": result,
                        "payments": repo.rows(
                            "SELECT * FROM med.retail_payment WHERE sale_id=:id "
                            "ORDER BY created_at,id",
                            {"id": sid},
                        ),
                    }
                )
            )

    def cancel(self, ctx: TenantContext, sid: UUID, body: Transition, key: str) -> dict[str, Any]:
        entity = self.scope(ctx, sid, "retail.sell")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "retail.sell")
            sale = one(repo, "SELECT * FROM med.retail_sale WHERE id=:id FOR UPDATE", {"id": sid})
            version(sale, body.expected_version)
            if sale["state"] != "RESERVED":
                raise BusinessError("INVALID_STATE", "已收款零售单不能直接取消，请完成或退款", 409)
            reservation = one(
                repo,
                "SELECT * FROM med.reservation WHERE id=:id FOR UPDATE",
                {"id": sale["reservation_id"]},
            )
            self.inventory._release(
                repo,
                ctx,
                sale["reservation_id"],
                ReleaseRequest(expected_version=str(reservation["version"]), reason=body.reason),
                retail=True,
                permission="retail.sell",
            )
            repo.execute(
                "UPDATE med.inventory_demand SET state='CANCELLED' WHERE id=:id", {"id": sid}
            )
            repo.execute(
                "UPDATE med.retail_sale SET state='CANCELLED',version=version+1 WHERE id=:id",
                {"id": sid},
            )
            audit(repo, ctx, "retail_sale", sid, "retail.cancelled", body.model_dump(mode="json"))
            return {"id": str(sid), "state": "CANCELLED", "version": str(sale["version"] + 1)}

        return command(
            self.uow, ctx, "retail.cancel", str(sid), key, body.model_dump(mode="json"), run
        )
