import json
from datetime import datetime, time
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from medinv.modules.catalog.application.admission import enforce
from medinv.modules.catalog.application.document_master import attach
from medinv.modules.catalog.application.entity_settings import entity_product_allowed
from medinv.modules.catalog.application.packaging import trade
from medinv.modules.catalog.application.profile import PROFILE_SELECT, project_business_identity
from medinv.modules.inventory.application.service import InventoryService
from medinv.modules.inventory.domain.decimal_values import money_for_posting
from medinv.modules.procurement.api.schemas import OrderLine, Transition
from medinv.modules.procurement.application.document_projection import capture
from medinv.modules.procurement.application.service import (
    ZONE,
    document_number,
    encode,
    one,
    version,
)
from medinv.modules.sales.api.schemas import CreditSet, SalesCreate
from medinv.modules.sales.application import amendments
from medinv.modules.sales.application.queries import SalesQueries
from medinv.modules.workspace.drafts import finish_draft
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SalesService(SalesQueries):
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.inventory = InventoryService(uow)

    def scope(self, ctx: TenantContext, permission: str, order_id: UUID) -> None:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id FROM med.sales_order WHERE id=:id",
                {"id": order_id},
            )
            ctx.require(permission, row["entity_id"])

    def entity(self, repo: SqlSession, ctx: TenantContext, entity: UUID, permission: str) -> None:
        ctx.require(permission, entity)
        row = one(repo, "SELECT status FROM med.legal_entity WHERE id=:e FOR UPDATE", {"e": entity})
        if row["status"] != "ACTIVE":
            raise BusinessError("ENTITY_UNAVAILABLE", "经营主体已停用", 409)

    def customer(self, repo: SqlSession, entity: UUID, customer: UUID) -> dict[str, Any]:
        row = one(
            repo,
            "SELECT * FROM med.party WHERE id=:id AND entity_id=:e FOR SHARE",
            {"id": customer, "e": entity},
        )
        day = self.now(repo).astimezone(ZONE).date()
        if (
            row["kind"] not in {"CUSTOMER", "BOTH"}
            or row["state"] != "APPROVED"
            or not row["valid_from"] <= day <= row["valid_until"]
        ):
            raise BusinessError("CUSTOMER_INELIGIBLE", "客户未审核、停用或资质不在有效期内", 422)
        return row

    @staticmethod
    def now(repo: SqlSession) -> datetime:
        return cast(datetime, one(repo, "SELECT clock_timestamp() AS now", {})["now"])

    def products(
        self,
        repo: SqlSession,
        entity: UUID,
        ids: list[UUID],
        customer: dict[str, Any],
        *,
        require_stock_gate: bool = False,
    ) -> dict[UUID, dict[str, Any]]:
        entity_product_allowed(repo, entity, ids, "sale")
        rows = repo.rows(
            f"""SELECT s.*,p.name,p.specification,p.approval_number,p.manufacturer_name,
            {PROFILE_SELECT},p.code AS product_code,
            p.approval_state,p.restricted_category,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current,p.category,
            p.drug_sale_class
            FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
            WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p""",
            {"ids": sorted(set(ids))},
        )
        if len(rows) != len(set(ids)):
            raise BusinessError("SKU_NOT_FOUND", "商品不可见或不存在", 404)
        for row in rows:
            if (
                row["approval_state"] != "APPROVED"
                or row["restricted_category"] is not None
                or row["serial_required"]
                or not row["lot_required"]
                or not row["qualification_current"]
                or row["category"] not in customer["categories"]
            ):
                raise BusinessError(
                    "PRODUCT_INELIGIBLE", "商品资质、客户经营范围或批次管理不符", 422
                )
        gates = repo.rows(
            """SELECT sku_id,blocked FROM med.stock_gate
            WHERE entity_id=:e AND sku_id=ANY(:ids) ORDER BY sku_id FOR SHARE""",
            {"e": entity, "ids": sorted(set(ids))},
        )
        # Wholesale demand may precede the first purchase. Missing stock history
        # is not a freeze; actual allocation still requires gates and balances.
        if any(g["blocked"] for g in gates) or (require_stock_gate and len(gates) != len(set(ids))):
            raise BusinessError("SKU_BLOCKED", "商品已冻结或尚不具备库存作业条件", 409)
        project_business_identity(repo, rows)
        enforce(repo, entity, "WHOLESALE", customer, rows)
        return {row["id"]: row for row in rows}

    def locked_order(
        self,
        repo: SqlSession,
        ctx: TenantContext,
        oid: UUID,
        permission: str,
        validate: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[UUID, dict[str, Any]]]:
        hint = one(repo, "SELECT entity_id FROM med.sales_order WHERE id=:id", {"id": oid})
        self.entity(repo, ctx, hint["entity_id"], permission)
        order = one(repo, "SELECT * FROM med.sales_order WHERE id=:id FOR UPDATE", {"id": oid})
        lines = repo.rows(
            "SELECT * FROM med.sales_order_line WHERE order_id=:id ORDER BY line_no", {"id": oid}
        )
        skus = {}
        if validate:
            customer = self.customer(repo, order["entity_id"], order["customer_id"])
            skus = self.products(
                repo, order["entity_id"], [entry["sku_id"] for entry in lines], customer
            )
        return order, lines, skus

    def credit_lock(
        self, repo: SqlSession, entity: UUID, customer: UUID, validate: bool = True
    ) -> dict[str, Any]:
        rows = repo.rows(
            """SELECT * FROM med.credit_account
            WHERE entity_id=:e AND customer_id=:c FOR UPDATE""",
            {"e": entity, "c": customer},
        )
        if not rows:
            raise BusinessError("CREDIT_NOT_CONFIGURED", "请先由管理员设置客户信用额度", 409)
        row = rows[0]
        if validate:
            day = self.now(repo).astimezone(ZONE).date()
            if row["valid_until"] < day:
                raise BusinessError("CREDIT_EXPIRED", "客户信用授权已过期", 409)
            overdue = repo.rows(
                """SELECT id FROM med.settlement_item
                WHERE entity_id=:e AND party_id=:c AND kind='AR' AND due_date<:day
                AND outstanding_amount>0 LIMIT 1""",
                {"e": entity, "c": customer, "day": day},
            )
            if overdue:
                raise BusinessError("CREDIT_OVERDUE", "客户存在逾期应收，暂停新增履约", 409)
        return row

    def credit(self, ctx: TenantContext, entity: UUID, customer: UUID) -> dict[str, Any]:
        ctx.require("sales.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            one(
                repo,
                "SELECT id FROM med.party WHERE id=:c AND entity_id=:e",
                {"c": customer, "e": entity},
            )
            rows = repo.rows(
                "SELECT * FROM med.credit_account WHERE entity_id=:e AND customer_id=:c",
                {"e": entity, "c": customer},
            )
            if not rows:
                return {
                    "version": "0",
                    "credit_limit": "0.00",
                    "exposure": "0.00",
                    "receivable": "0.00",
                    "available": "0.00",
                    "valid_until": None,
                }
            row = rows[0]
            return dict(
                encode(
                    {
                        **row,
                        "version": str(row["version"]),
                        "available": row["credit_limit"] - row["exposure"] - row["receivable"],
                    }
                )
            )

    def set_credit(
        self, ctx: TenantContext, entity: UUID, customer: UUID, body: CreditSet, key: str
    ) -> dict[str, Any]:
        ctx.require("sales.credit", entity)

        def execute(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, entity, "sales.credit")
            self.customer(repo, entity, customer)
            if body.valid_until < self.now(repo).astimezone(ZONE).date():
                raise BusinessError("CREDIT_EXPIRED", "信用授权日期不能已过期", 422)
            rows = repo.rows(
                """SELECT * FROM med.credit_account
                WHERE entity_id=:e AND customer_id=:c FOR UPDATE""",
                {"e": entity, "c": customer},
            )
            row: dict[str, Any] = (
                rows[0]
                if rows
                else {"version": 0, "exposure": Decimal(0), "receivable": Decimal(0)}
            )
            version(row, body.expected_version)
            if Decimal(body.credit_limit) < row["exposure"] + row["receivable"]:
                raise BusinessError("CREDIT_LIMIT_EXCEEDED", "额度不能低于已占敞口和应收", 409)
            repo.execute(
                """INSERT INTO med.credit_account
                (tenant_id,entity_id,customer_id,credit_limit,valid_until,updated_by)
                VALUES(:t,:e,:c,:lim,:until,:a) ON CONFLICT(tenant_id,entity_id,customer_id)
                DO UPDATE SET credit_limit=EXCLUDED.credit_limit,valid_until=EXCLUDED.valid_until,
                updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp(),
                version=credit_account.version+1""",
                {
                    "t": ctx.tenant_id,
                    "e": entity,
                    "c": customer,
                    "lim": Decimal(body.credit_limit),
                    "until": body.valid_until,
                    "a": ctx.principal_id,
                },
            )
            audit(
                repo,
                ctx,
                "credit_account",
                customer,
                "sales.credit_changed",
                {"entity_id": str(entity), **body.model_dump(mode="json")},
            )
            return {"version": str(row["version"] + 1)}

        return command(
            self.uow,
            ctx,
            "sales.credit",
            f"{entity}:{customer}",
            key,
            body.model_dump(mode="json"),
            execute,
        )

    def prepare_lines(
        self,
        repo: SqlSession,
        items: list[OrderLine],
        skus: dict[UUID, dict[str, Any]],
        ctx: TenantContext,
        entity: UUID,
    ) -> tuple[list[dict[str, Any]], Decimal]:
        attach(repo, ctx, entity, list(skus.values()))
        prepared = []
        try:
            for item in items:
                qty, package = trade(repo, skus[item.sku_id], item.packaging_id, item.qty)
                gross = money_for_posting(Decimal(item.qty) * Decimal(item.unit_price))
                net = money_for_posting(gross / (1 + Decimal(item.tax_rate)))
                prepared.append(
                    {
                        **item.model_dump(),
                        "q": qty,
                        "g": gross,
                        "net": net,
                        "trade": Decimal(item.qty),
                        "package": json.dumps(package),
                        "snapshot": json.dumps(capture(skus[item.sku_id])),
                    }
                )
            total = money_for_posting(sum((entry["g"] for entry in prepared), Decimal(0)))
        except ValueError as exc:
            raise BusinessError("INVALID_AMOUNT", "数量精度或金额超出支持范围", 422) from exc
        return prepared, total

    def insert_lines(
        self, repo: SqlSession, ctx: TenantContext, oid: UUID, prepared: list[dict[str, Any]]
    ) -> None:
        for index, prepared_item in enumerate(prepared, 1):
            repo.execute(
                """INSERT INTO med.sales_order_line(tenant_id,id,order_id,line_no,
                sku_id,qty,unit_price,tax_rate,gross_amount,net_amount,trade_qty,package_snapshot,document_snapshot)
                VALUES(:t,:id,:o,:n,:sku_id,:q,:unit_price,:tax_rate,:g,:net,
                  :trade,CAST(:package AS jsonb),CAST(:snapshot AS jsonb))""",
                {**prepared_item, "t": ctx.tenant_id, "id": uuid4(), "o": oid, "n": index},
            )

    def create(self, ctx: TenantContext, body: SalesCreate, key: str) -> dict[str, Any]:
        ctx.require("sales.write", body.entity_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, body.entity_id, "sales.write")
            customer = self.customer(repo, body.entity_id, body.customer_id)
            skus = self.products(
                repo, body.entity_id, [entry.sku_id for entry in body.lines], customer
            )
            one(
                repo,
                """SELECT id FROM med.warehouse
                WHERE id=:w AND entity_id=:e AND kind='PHYSICAL'
                AND archived_at IS NULL FOR SHARE""",
                {"w": body.warehouse_id, "e": body.entity_id},
            )
            prepared, total = self.prepare_lines(repo, body.lines, skus, ctx, body.entity_id)
            oid = uuid4()
            number = document_number("SO", oid)
            repo.execute(
                """INSERT INTO med.sales_order(tenant_id,id,entity_id,customer_id,
                warehouse_id,number,delivery_address,minimum_shelf_days,credit_days,gross_amount,
                created_by) VALUES(:t,:id,:e,:c,:w,:n,:address,:days,:credit,:gross,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": oid,
                    "e": body.entity_id,
                    "c": body.customer_id,
                    "w": body.warehouse_id,
                    "n": number,
                    "address": body.delivery_address,
                    "days": body.minimum_shelf_days,
                    "credit": body.credit_days,
                    "gross": total,
                    "a": ctx.principal_id,
                },
            )
            self.insert_lines(repo, ctx, oid, prepared)
            audit(
                repo,
                ctx,
                "sales_order",
                oid,
                "sales.created",
                {**body.model_dump(mode="json"), "agreement": amendments.snapshot(repo, oid)},
            )
            finish_draft(repo, ctx, body.editor_draft, "sale", body.entity_id, oid)
            return {"id": str(oid), "number": number, "version": "1"}

        return command(
            self.uow,
            ctx,
            "sales.create",
            str(body.entity_id),
            key,
            body.model_dump(mode="json"),
            execute,
        )

    def transition(
        self, ctx: TenantContext, oid: UUID, body: Transition, key: str, approve: bool
    ) -> dict[str, Any]:
        permission = "sales.approve" if approve else "sales.write"
        self.scope(ctx, permission, oid)

        def execute(repo: SqlSession) -> dict[str, Any]:
            order, lines, _ = self.locked_order(repo, ctx, oid, permission, validate=approve)
            version(order, body.expected_version)
            if approve:
                if order["state"] != "DRAFT":
                    raise BusinessError("INVALID_STATE", "仅草稿销售单可审核", 409)
                if (
                    order["created_by"] == ctx.principal_id
                    or ctx.principal_id in order["revision_editors"]
                ):
                    raise BusinessError(
                        "SEPARATION_REQUIRED", "制单人及本轮修改人不能审核销售单", 403
                    )
                credit = self.credit_lock(repo, order["entity_id"], order["customer_id"])
                if (
                    credit["exposure"] + credit["receivable"] + order["gross_amount"]
                    > credit["credit_limit"]
                ):
                    raise BusinessError("CREDIT_LIMIT_EXCEEDED", "客户可用信用额度不足", 409)
                customer = self.customer(repo, order["entity_id"], order["customer_id"])
                until = datetime.combine(customer["valid_until"], time.max, ZONE)
                demand = repo.rows(
                    """INSERT INTO med.inventory_demand(tenant_id,id,entity_id,
                    warehouse_id,source_type,state,checks_completed,eligible_until)
                    VALUES(:t,:id,:e,:w,'SALES_ORDER','APPROVED',true,:until)
                    ON CONFLICT(tenant_id,id) DO UPDATE SET warehouse_id=EXCLUDED.warehouse_id,
                      state='APPROVED',checks_completed=true,eligible_until=EXCLUDED.eligible_until,
                      version=inventory_demand.version+1
                    WHERE inventory_demand.source_type='SALES_ORDER'
                      AND inventory_demand.entity_id=EXCLUDED.entity_id
                      AND inventory_demand.state='CANCELLED'
                      AND NOT inventory_demand.checks_completed
                      AND NOT EXISTS (SELECT 1 FROM med.reservation r
                        WHERE r.tenant_id=EXCLUDED.tenant_id AND r.source_id=EXCLUDED.id)
                    RETURNING id""",
                    {
                        "t": ctx.tenant_id,
                        "id": oid,
                        "e": order["entity_id"],
                        "w": order["warehouse_id"],
                        "until": until,
                    },
                )
                if not demand:
                    raise BusinessError(
                        "SALES_ALREADY_STARTED", "销售单已有履约记录，不能重新审核", 409
                    )
                for line in lines:
                    repo.execute(
                        """INSERT INTO med.inventory_demand_line
                        (tenant_id,id,demand_id,sku_id,requested_qty) VALUES(:t,:id,:o,:s,:q)""",
                        {
                            "t": ctx.tenant_id,
                            "id": line["id"],
                            "o": oid,
                            "s": line["sku_id"],
                            "q": line["qty"],
                        },
                    )
                repo.execute(
                    """UPDATE med.credit_account SET exposure=exposure+:g,
                    version=version+1 WHERE entity_id=:e AND customer_id=:c""",
                    {
                        "g": order["gross_amount"],
                        "e": order["entity_id"],
                        "c": order["customer_id"],
                    },
                )
                repo.execute(
                    """UPDATE med.sales_order SET state='APPROVED',version=version+1,
                    remaining_exposure=gross_amount,approved_by=:a,approved_at=clock_timestamp(),
                    revision_editors='{}',
                    customer_snapshot=CAST(:snapshot AS jsonb) WHERE id=:id""",
                    {
                        "id": oid,
                        "a": ctx.principal_id,
                        "snapshot": json.dumps(
                            encode(
                                {
                                    "customer": customer,
                                    "credit_limit": credit["credit_limit"],
                                    "credit_version": str(credit["version"]),
                                    "policy": "LIMIT_AND_OVERDUE_V1",
                                }
                            )
                        ),
                    },
                )
                state = "APPROVED"
            else:
                if order["state"] not in {"DRAFT", "APPROVED"}:
                    raise BusinessError("INVALID_STATE", "当前销售单不能取消或关闭余量", 409)
                active = repo.rows(
                    """SELECT id FROM med.shipment WHERE order_id=:id
                    AND state NOT IN ('CANCELLED','POSTED') LIMIT 1""",
                    {"id": oid},
                )
                if active:
                    raise BusinessError("ACTIVE_SHIPMENT", "请先取消未发货子单并释放预占", 409)
                fulfilled = repo.rows(
                    "SELECT id FROM med.shipment WHERE order_id=:id AND state='POSTED'", {"id": oid}
                )
                state = "CLOSED" if fulfilled else "CANCELLED"
                if order["state"] == "APPROVED":
                    self.credit_lock(repo, order["entity_id"], order["customer_id"], False)
                    repo.execute(
                        """UPDATE med.credit_account SET exposure=exposure-:g,
                        version=version+1 WHERE entity_id=:e AND customer_id=:c""",
                        {
                            "g": order["remaining_exposure"],
                            "e": order["entity_id"],
                            "c": order["customer_id"],
                        },
                    )
                    repo.execute(
                        "UPDATE med.inventory_demand SET state=:s,version=version+1 WHERE id=:id",
                        {"s": "CLOSED" if fulfilled else "CANCELLED", "id": oid},
                    )
                repo.execute(
                    """UPDATE med.sales_order SET state=:s,version=version+1,
                    remaining_exposure=0 WHERE id=:id""",
                    {"s": state, "id": oid},
                )
            audit(
                repo,
                ctx,
                "sales_order",
                oid,
                "sales." + state.lower(),
                {**body.model_dump(mode="json"), "agreement": amendments.snapshot(repo, oid)},
            )
            return {"id": str(oid), "state": state, "version": str(order["version"] + 1)}

        return command(
            self.uow,
            ctx,
            "sales.approve" if approve else "sales.close",
            str(oid),
            key,
            body.model_dump(mode="json"),
            execute,
        )
