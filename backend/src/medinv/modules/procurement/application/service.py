import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.schemas import DTO
from medinv.modules.catalog.application.admission import enforce
from medinv.modules.catalog.application.document_master import attach
from medinv.modules.catalog.application.entity_settings import entity_product_allowed
from medinv.modules.catalog.application.master_config import current_schema
from medinv.modules.catalog.application.packaging import trade
from medinv.modules.catalog.application.profile import PROFILE_SELECT, project_business_identity
from medinv.modules.inventory.domain.decimal_values import money_for_posting, quantity
from medinv.modules.procurement.api.schemas import (
    Approval,
    InspectionCreate,
    OrderCreate,
    OrderLine,
    ReceiptCreate,
    ReceiptPost,
    Transition,
)
from medinv.modules.procurement.application.amendments import snapshot
from medinv.modules.procurement.application.conclusions import record_note
from medinv.modules.procurement.application.document_projection import (
    capture,
    project_detail,
    public_documents,
)
from medinv.modules.workspace.drafts import finish_draft
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

ZONE = ZoneInfo("Asia/Shanghai")


def encode(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder={Decimal: lambda d: format(d, "f")})


def one(repo: SqlSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    rows = repo.rows(sql, params)
    if not rows:
        raise BusinessError("NOT_FOUND", "业务记录不可见或不存在", 404)
    return rows[0]


def version(row: dict[str, Any], expected: str) -> None:
    if str(row["version"]) != expected:
        raise BusinessError("VERSION_CONFLICT", "单据已变化，请刷新后操作", 409)


def today(repo: SqlSession) -> Any:
    return one(repo, "SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS day", {})[
        "day"
    ]


def document_number(prefix: str, identifier: UUID) -> str:
    return prefix + "-" + identifier.hex.upper()


class ProcurementService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def scope(self, ctx: TenantContext, permission: str, order_id: UUID) -> None:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id FROM med.purchase_order WHERE id=:id",
                {"id": order_id},
            )
            ctx.require(permission, row["entity_id"])

    def receipt_order(self, ctx: TenantContext, receipt_id: UUID) -> UUID:
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT order_id FROM med.receipt WHERE id=:id",
                {"id": receipt_id},
            )
            return UUID(str(row["order_id"]))

    def run(
        self, ctx: TenantContext, name: str, target: UUID | str, body: DTO, key: str, handler: Any
    ) -> dict[str, Any]:
        return command(self.uow, ctx, name, str(target), key, body.model_dump(mode="json"), handler)

    def entity(self, repo: SqlSession, ctx: TenantContext, entity: UUID) -> None:
        # Coarse per-entity serialization is intentional for this initial procurement slice.
        row = one(repo, "SELECT status FROM med.legal_entity WHERE id=:e FOR UPDATE", {"e": entity})
        if row["status"] != "ACTIVE":
            raise BusinessError("ENTITY_UNAVAILABLE", "经营主体已停用", 409)

    def supplier(
        self, repo: SqlSession, entity: UUID, supplier: UUID, validate: bool = True
    ) -> dict[str, Any]:
        row = one(
            repo,
            "SELECT * FROM med.party WHERE id=:id AND entity_id=:e FOR SHARE",
            {"id": supplier, "e": entity},
        )
        if validate and (
            row["state"] != "APPROVED"
            or row["kind"] not in {"SUPPLIER", "BOTH"}
            or not row["valid_from"] <= today(repo) <= row["valid_until"]
        ):
            raise BusinessError("SUPPLIER_INELIGIBLE", "供应商未审核、停用或资质不在有效期内", 422)
        return row

    def products(
        self,
        repo: SqlSession,
        ctx: TenantContext,
        entity: UUID,
        sku_ids: list[UUID],
        supplier: dict[str, Any],
        validate: bool = True,
    ) -> dict[UUID, dict[str, Any]]:
        entity_product_allowed(repo, entity, sku_ids, "purchase")
        ids = sorted(set(sku_ids))
        rows = repo.rows(
            f"""SELECT s.*,p.name,p.category,p.manufacturer_name,p.specification,{PROFILE_SELECT},
            p.approval_number,p.code AS product_code,
            p.approval_state,p.restricted_category,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current
            FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,
            s.product_id)
            WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p""",
            {"ids": ids},
        )
        if len(rows) != len(ids):
            raise BusinessError("SKU_NOT_FOUND", "商品不可见或不存在", 404)
        for row in rows:
            if validate and (
                row["approval_state"] != "APPROVED"
                or not row["qualification_current"]
                or row["category"] not in supplier["categories"]
                or row["serial_required"]
                or row["restricted_category"]
            ):
                raise BusinessError(
                    "PRODUCT_INELIGIBLE", "商品未审核、资质失效、不在供应商范围或类别未开放", 422
                )
        # A new ordinary SKU gets its gate once; existing freezes are never reset.
        for sid in ids:
            repo.execute(
                """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,
                sku_id)
                VALUES (:t,:id,:a,:a,:e,:s) ON CONFLICT(tenant_id,entity_id,sku_id) DO
                NOTHING""",
                {"t": ctx.tenant_id, "id": uuid4(), "a": ctx.principal_id, "e": entity, "s": sid},
            )
        gates = repo.rows(
            """SELECT blocked FROM med.stock_gate WHERE entity_id=:e AND sku_id=ANY(:ids)
            ORDER BY sku_id FOR SHARE""",
            {"e": entity, "ids": ids},
        )
        if validate and any(g["blocked"] for g in gates):
            raise BusinessError("SKU_BLOCKED", "商品已冻结", 409)
        project_business_identity(repo, rows, enforce_rules=validate)
        if validate:
            enforce(repo, entity, "PURCHASE", supplier, rows)
        return {r["id"]: r for r in rows}

    def locked_order(
        self,
        repo: SqlSession,
        ctx: TenantContext,
        order_id: UUID,
        permission: str,
        validate: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[UUID, dict[str, Any]]]:
        hint = one(repo, "SELECT * FROM med.purchase_order WHERE id=:id", {"id": order_id})
        ctx.require(permission, hint["entity_id"])
        self.entity(repo, ctx, hint["entity_id"])
        # Re-read after the entity lock: a preceding revision may replace the supplier/lines.
        hint = one(repo, "SELECT * FROM med.purchase_order WHERE id=:id", {"id": order_id})
        supplier = self.supplier(repo, hint["entity_id"], hint["supplier_id"], validate)
        lines = repo.rows(
            "SELECT * FROM med.purchase_order_line WHERE order_id=:id ORDER BY line_no",
            {"id": order_id},
        )
        skus = self.products(
            repo, ctx, hint["entity_id"], [r["sku_id"] for r in lines], supplier, validate
        )
        order = one(
            repo, "SELECT * FROM med.purchase_order WHERE id=:id FOR UPDATE", {"id": order_id}
        )
        return order, lines, skus

    def options(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int = 0,
        include_skus: bool = True,
        include_parties: bool = True,
    ) -> dict[str, Any]:
        ctx.require("purchase.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            suppliers = (
                repo.rows(
                    """SELECT id,name FROM med.party WHERE entity_id=:e AND state='APPROVED'
                AND kind IN ('SUPPLIER','BOTH')
                AND (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
                  BETWEEN valid_from AND valid_until ORDER BY name,id LIMIT 201 OFFSET :offset""",
                    {"e": entity, "offset": offset},
                )
                if include_parties
                else []
            )

            skus = (
                repo.rows(
                    """SELECT s.id,p.name,s.code,s.base_unit,s.qty_scale FROM med.sku s
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                WHERE p.approval_state='APPROVED' AND p.restricted_category IS NULL
                AND NOT s.serial_required
                AND med.product_qualification_current(p)
                AND NOT EXISTS (SELECT 1 FROM med.stock_gate g WHERE g.entity_id=:e
                  AND g.sku_id=s.id AND g.blocked)
                ORDER BY p.name,s.id LIMIT 201 OFFSET :offset""",
                    {"e": entity, "offset": offset},
                )
                if include_skus
                else []
            )
            warehouses = repo.rows(
                """SELECT id,name FROM med.warehouse WHERE entity_id=:e AND kind='PHYSICAL'
                AND archived_at IS NULL
                ORDER BY name,id LIMIT 201 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            locations = repo.rows(
                """SELECT l.id,l.code,coalesce(l.name,l.code) AS name,l.warehouse_id
                FROM med.location l JOIN med.warehouse w
                ON (w.tenant_id,w.id)=(l.tenant_id,l.warehouse_id)
                WHERE w.entity_id=:e AND w.kind='PHYSICAL' AND w.archived_at IS NULL
                ORDER BY l.code,l.id LIMIT 201 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            more = any(len(v) > 200 for v in (suppliers, skus, warehouses, locations))
            return cast(
                dict[str, Any],
                encode(
                    {
                        "has_more": more,
                        "next_offset": offset + 200 if more else None,
                        "suppliers": suppliers[:200],
                        "skus": skus[:200],
                        "warehouses": warehouses[:200],
                        "locations": locations[:200],
                    }
                ),
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
        ctx.require("purchase.read", entity)
        filters, params = document_filters(
            "o",
            "concat_ws(' ',o.number,p.name,p.code)",
            {
                "DRAFT": "o.approval_state='DRAFT'",
                "CANCELLED": "o.approval_state='CANCELLED'",
                **{
                    s: f"o.approval_state='APPROVED' AND o.fulfillment_state='{s}'"
                    for s in ("OPEN", "RECEIVED", "CLOSED")
                },
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
            filters += " AND o.supplier_id=:filter_party"
            params["filter_party"] = party_id
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT o.*,p.name AS supplier_name,w.name AS warehouse_name,count(*) OVER()
                AS total
                FROM med.purchase_order o JOIN med.party p ON (p.tenant_id,
                p.id)=(o.tenant_id,o.supplier_id)
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
                WHERE o.entity_id=:e {filters}
                ORDER BY o.created_at DESC,o.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(self, ctx: TenantContext, order_id: UUID) -> dict[str, Any]:
        self.scope(ctx, "purchase.read", order_id)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            order = one(
                repo,
                """SELECT o.*,p.name AS supplier_name,w.name AS warehouse_name,
                CASE WHEN :sales_read THEN so.number ELSE NULL END AS source_sales_number
                FROM med.purchase_order o JOIN med.party p
                ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
                LEFT JOIN med.sales_order so
                  ON (so.tenant_id,so.id)=(o.tenant_id,o.source_sales_order_id)
                WHERE o.id=:id""",
                {"id": order_id, "sales_read": "sales.read" in ctx.permissions},
            )
            lines = repo.rows(
                """SELECT l.*,p.name,s.base_unit,s.qty_scale,
                l.qty-l.received_qty-l.closed_qty AS remaining_qty,
                l.qty-l.received_qty AS unreceived_qty
                FROM med.purchase_order_line l
                JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                WHERE order_id=:id ORDER BY line_no""",
                {"id": order_id},
            )
            receipts = repo.rows(
                "SELECT * FROM med.receipt WHERE order_id=:id ORDER BY received_at,id",
                {"id": order_id},
            )
            if len(receipts) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "到货记录超过当前详情上限", 422)
            for receipt in receipts:
                receipt["version"] = str(receipt["version"])
                receipt["lines"] = repo.rows(
                    """SELECT l.*,lot.lot_no,lot.mfg_date,lot.raw_expiry,
                    p.name,s.base_unit,loc.code AS location,
                    l.qty-l.qualified_qty-l.rejected_qty AS pending_qty,
                    l.qty-l.posted_qty-l.rejected_returned_qty AS custody_qty,
                    l.rejected_qty-l.rejected_returned_qty AS rejected_pending_qty
                    FROM med.receipt_line l JOIN med.lot lot ON (lot.tenant_id,
                    lot.id)=(l.tenant_id,l.lot_id)
                    JOIN med.purchase_order_line ol
                      ON (ol.tenant_id,ol.id)=(l.tenant_id,l.order_line_id)
                    JOIN med.location loc ON (loc.tenant_id,loc.id)=(l.tenant_id,l.location_id)
                    JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
                    JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                    WHERE receipt_id=:id ORDER BY ol.line_no,lot.lot_no,l.id""",
                    {"id": receipt["id"]},
                )
                receipt["postings"] = repo.rows(
                    """SELECT rp.*,i.note,i.created_by AS inspector_id
                    FROM med.receipt_posting rp JOIN med.inspection i
                    ON (i.tenant_id,i.id)=(rp.tenant_id,rp.inspection_id)
                    WHERE i.receipt_id=:id ORDER BY i.inspected_at,rp.id""",
                    {"id": receipt["id"]},
                )
                for rp in receipt["postings"]:
                    rp["version"] = str(rp["version"])
                    rp["lines"] = repo.rows(
                        """SELECT il.receipt_line_id,il.qualified_qty,p.name,l.lot_no,s.base_unit
                        FROM med.inspection_line il JOIN med.receipt_line rl
                        ON (rl.tenant_id,rl.id)=(il.tenant_id,il.receipt_line_id)
                        JOIN med.purchase_order_line ol
                          ON (ol.tenant_id,ol.id)=(rl.tenant_id,rl.order_line_id)
                        JOIN med.sku s ON (s.tenant_id,s.id)=(rl.tenant_id,rl.sku_id)
                        JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                        JOIN med.lot l ON (l.tenant_id,l.id)=(rl.tenant_id,rl.lot_id)
                        WHERE il.inspection_id=:i AND il.qualified_qty>0
                        ORDER BY ol.line_no,l.lot_no,rl.id""",
                        {"i": rp["inspection_id"]},
                    )
                receipt["inspections"] = repo.rows(
                    """SELECT i.id,i.note,i.inspected_at,il.receipt_line_id,
                    il.qualified_qty,il.rejected_qty,
                    p.name,l.lot_no,s.base_unit FROM med.inspection i
                    JOIN med.inspection_line il
                    ON (il.tenant_id,il.inspection_id)=(i.tenant_id,i.id)
                    JOIN med.receipt_line rl
                    ON (rl.tenant_id,rl.id)=(il.tenant_id,il.receipt_line_id)
                    JOIN med.purchase_order_line ol
                      ON (ol.tenant_id,ol.id)=(rl.tenant_id,rl.order_line_id)
                    JOIN med.sku s ON (s.tenant_id,s.id)=(rl.tenant_id,rl.sku_id)
                    JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                    JOIN med.lot l ON (l.tenant_id,l.id)=(rl.tenant_id,rl.lot_id)
                    WHERE i.receipt_id=:r ORDER BY i.inspected_at,i.id,ol.line_no,l.lot_no,rl.id""",
                    {"r": receipt["id"]},
                )
                receipt["rejected_handovers"] = repo.rows(
                    """SELECT h.id,h.handover_ref,h.recipient,h.reason,h.created_at,
                    h.created_by,hl.receipt_line_id,hl.qty,p.name,lot.lot_no,s.base_unit
                    FROM med.purchase_rejected_handover h
                    JOIN med.purchase_rejected_handover_line hl
                      ON (hl.tenant_id,hl.handover_id)=(h.tenant_id,h.id)
                    JOIN med.receipt_line rl
                      ON (rl.tenant_id,rl.id)=(hl.tenant_id,hl.receipt_line_id)
                    JOIN med.sku s ON (s.tenant_id,s.id)=(rl.tenant_id,rl.sku_id)
                    JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                    JOIN med.lot lot ON (lot.tenant_id,lot.id)=(rl.tenant_id,rl.lot_id)
                    WHERE h.receipt_id=:r ORDER BY h.created_at,h.id,hl.id""",
                    {"r": receipt["id"]},
                )
            project_detail(lines, receipts, current_schema(repo)["payload"], ctx.permissions)
            return cast(
                dict[str, Any],
                encode(
                    public_documents(
                        {
                            "order": {**order, "version": str(order["version"])},
                            "lines": lines,
                            "receipts": receipts,
                        }
                    )
                ),
            )

    def insert_lines(
        self,
        repo: SqlSession,
        ctx: TenantContext,
        oid: UUID,
        lines: list[OrderLine],
        skus: dict[UUID, dict[str, Any]],
    ) -> None:
        entity = one(repo, "SELECT entity_id FROM med.purchase_order WHERE id=:id", {"id": oid})[
            "entity_id"
        ]
        attach(repo, ctx, entity, list(skus.values()))
        for index, line in enumerate(lines, 1):
            try:
                qty, package = trade(repo, skus[line.sku_id], line.packaging_id, line.qty)
                gross = net = None
                if line.packaging_id is not None:
                    gross = money_for_posting(Decimal(line.qty) * Decimal(line.unit_price))
                    net = money_for_posting(gross / (1 + Decimal(line.tax_rate)))
            except ValueError as exc:
                raise BusinessError("INVALID_QUANTITY", "订购数量不符合商品精度", 422) from exc
            repo.execute(
                """INSERT INTO med.purchase_order_line(tenant_id,id,order_id,line_no,sku_id,qty,
                unit_price,tax_rate,trade_qty,package_snapshot,gross_amount,net_amount,document_snapshot)
                VALUES (:t,:id,:o,:n,:s,:q,:p,:r,:trade,CAST(:package AS jsonb),:gross,:net,
                CAST(:snapshot AS jsonb))""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "o": oid,
                    "n": index,
                    "s": line.sku_id,
                    "q": qty,
                    "p": Decimal(line.unit_price),
                    "r": Decimal(line.tax_rate),
                    "trade": Decimal(line.qty) if line.packaging_id else None,
                    "package": json.dumps(package) if line.packaging_id else None,
                    "gross": gross if line.packaging_id else None,
                    "net": net if line.packaging_id else None,
                    "snapshot": json.dumps(capture(skus[line.sku_id])),
                },
            )

    def create_order(self, ctx: TenantContext, body: OrderCreate, key: str) -> dict[str, Any]:
        ctx.require("purchase.write", body.entity_id)

        return self.run(
            ctx,
            "purchase.create",
            body.entity_id,
            body,
            key,
            lambda repo: self.create_in_transaction(repo, ctx, body),
        )

    def create_in_transaction(
        self,
        repo: SqlSession,
        ctx: TenantContext,
        body: OrderCreate,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx.require("purchase.write", body.entity_id)
        self.entity(repo, ctx, body.entity_id)
        supplier = self.supplier(repo, body.entity_id, body.supplier_id)
        one(
            repo,
            """SELECT id FROM med.warehouse WHERE id=:w AND entity_id=:e AND
            kind='PHYSICAL' AND archived_at IS NULL FOR SHARE""",
            {"w": body.warehouse_id, "e": body.entity_id},
        )
        skus = self.products(
            repo, ctx, body.entity_id, [item.sku_id for item in body.lines], supplier
        )
        oid = uuid4()
        number = document_number("PO", oid)
        repo.execute(
            """INSERT INTO med.purchase_order(tenant_id,id,entity_id,supplier_id,
            warehouse_id,number,created_by,source_sales_order_id,source_sales_version)
            VALUES (:t,:id,:e,:s,:w,:n,:a,:source,:source_version)""",
            {
                "t": ctx.tenant_id,
                "id": oid,
                "e": body.entity_id,
                "s": body.supplier_id,
                "w": body.warehouse_id,
                "n": number,
                "a": ctx.principal_id,
                "source": source["id"] if source else None,
                "source_version": source["version"] if source else None,
            },
        )
        self.insert_lines(repo, ctx, oid, body.lines, skus)
        audit(
            repo,
            ctx,
            "purchase_order",
            oid,
            "purchase.created",
            {
                **body.model_dump(mode="json"),
                **(
                    {
                        "source_sales_order_id": str(source["id"]),
                        "source_sales_version": str(source["version"]),
                    }
                    if source
                    else {}
                ),
            },
        )
        finish_draft(repo, ctx, body.editor_draft, "purchase", body.entity_id, oid)
        return {"id": str(oid), "number": number, "version": "1"}

    def transition(
        self, ctx: TenantContext, order_id: UUID, body: Transition, key: str, approve: bool
    ) -> dict[str, Any]:
        permission = "purchase.approve" if approve else "purchase.write"
        self.scope(ctx, permission, order_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            order, _, _ = self.locked_order(repo, ctx, order_id, permission, validate=approve)
            version(order, body.expected_version)
            if order["approval_state"] != "DRAFT":
                raise BusinessError("INVALID_STATE", "仅草稿采购单可审核或取消", 409)
            if approve and (
                order["created_by"] == ctx.principal_id
                or ctx.principal_id in order["revision_editors"]
            ):
                raise BusinessError(
                    "SEPARATION_REQUIRED", "制单人及本轮修改人员不能审核通过采购单", 403
                )
            note = record_note(
                repo,
                ctx,
                "purchase_approval",
                body.conclusion if isinstance(body, Approval) and approve else None,
                body.reason,
                200,
            )
            state = "APPROVED" if approve else "CANCELLED"
            repo.execute(
                """UPDATE med.purchase_order SET approval_state=:state,version=version+1,
                revision_editors=CASE WHEN CAST(:state AS varchar)='APPROVED'
                  THEN ARRAY[]::uuid[] ELSE revision_editors END,
                approved_by=:actor,approved_at=clock_timestamp() WHERE id=:id""",
                {"state": state, "actor": ctx.principal_id, "id": order_id},
            )
            audit(
                repo,
                ctx,
                "purchase_order",
                order_id,
                "purchase." + state.lower(),
                {
                    **body.model_dump(mode="json"),
                    "revision_no": order["revision_no"],
                    "reason": note,
                    "agreement": snapshot(repo, order_id),
                },
            )
            return {"id": str(order_id), "version": str(order["version"] + 1), "state": state}

        return self.run(
            ctx, "purchase.approve" if approve else "purchase.cancel", order_id, body, key, execute
        )

    def close(
        self, ctx: TenantContext, order_id: UUID, body: Transition, key: str
    ) -> dict[str, Any]:
        self.scope(ctx, "purchase.write", order_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            # Closure must remain possible after supplier/product approval has expired.
            order, lines, _ = self.locked_order(
                repo, ctx, order_id, "purchase.write", validate=False
            )
            version(order, body.expected_version)
            if order["approval_state"] != "APPROVED" or order["fulfillment_state"] != "OPEN":
                raise BusinessError("INVALID_STATE", "仅已审核且尚未收齐的采购单可关闭余量", 409)
            remaining = [line for line in lines if line["qty"] > line["received_qty"]]
            if not remaining:
                raise BusinessError("NO_REMAINING_QUANTITY", "没有未到货余量可关闭", 409)
            # Existing entity lock serializes arrival/inspection/posting. Original order
            # quantities/prices stay intact for subsequent inspection and cost allocation.
            repo.execute(
                "UPDATE med.purchase_order_line SET closed_qty=qty-received_qty WHERE order_id=:id",
                {"id": order_id},
            )
            row = one(
                repo,
                """UPDATE med.purchase_order SET fulfillment_state='CLOSED',
                closed_by=:actor,closed_at=clock_timestamp(),close_reason=:reason,
                version=version+1 WHERE id=:id RETURNING closed_at,version""",
                {"id": order_id, "actor": ctx.principal_id, "reason": body.reason},
            )
            result = {
                "id": str(order_id),
                "version": str(row["version"]),
                "fulfillment_state": "CLOSED",
                "closed_by": str(ctx.principal_id),
                "closed_at": row["closed_at"].isoformat(),
                "close_reason": body.reason,
                "lines": [
                    {
                        "id": str(line["id"]),
                        "closed_qty": format(line["qty"] - line["received_qty"], "f"),
                    }
                    for line in remaining
                ],
            }
            audit(repo, ctx, "purchase_order", order_id, "purchase.closed", result)
            return result

        return self.run(ctx, "purchase.close", order_id, body, key, execute)

    def receive(
        self, ctx: TenantContext, order_id: UUID, body: ReceiptCreate, key: str
    ) -> dict[str, Any]:
        self.scope(ctx, "purchase.receive", order_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            order, lines, skus = self.locked_order(repo, ctx, order_id, "purchase.receive")
            version(order, body.expected_version)
            if order["approval_state"] != "APPROVED" or order["fulfillment_state"] != "OPEN":
                raise BusinessError("INVALID_STATE", "采购单尚未审核、已收齐或未收余量已关闭", 409)
            by_id = {item["id"]: item for item in lines}
            totals: dict[UUID, Decimal] = {}
            rid = uuid4()
            repo.execute(
                """INSERT INTO med.receipt(tenant_id,id,order_id,number,shipment_ref,
                transport_note,created_by)
                VALUES (:t,:id,:o,:n,:s,:note,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": rid,
                    "o": order_id,
                    "n": document_number("RC", rid),
                    "s": body.shipment_ref,
                    "note": body.transport_note,
                    "a": ctx.principal_id,
                },
            )
            for line in sorted(body.lines, key=lambda item: (str(item.order_line_id), item.lot_no)):
                if line.order_line_id not in by_id:
                    raise BusinessError("SOURCE_LINE_MISMATCH", "到货行不属于该采购单", 422)
                source = by_id[line.order_line_id]
                sku = skus[source["sku_id"]]
                try:
                    qty = quantity(line.qty, sku_scale=sku["qty_scale"])
                except ValueError as exc:
                    raise BusinessError("INVALID_QUANTITY", "到货数量不符合商品精度", 422) from exc
                totals[source["id"]] = totals.get(source["id"], Decimal(0)) + qty
                if source["received_qty"] + totals[source["id"]] > source["qty"]:
                    raise BusinessError("OVER_RECEIPT", "累计到货数量超过订购数量", 409)
                one(
                    repo,
                    "SELECT id FROM med.location WHERE id=:id AND warehouse_id=:w FOR SHARE",
                    {"id": line.location_id, "w": order["warehouse_id"]},
                )
                if line.mfg_date > today(repo):
                    raise BusinessError("INVALID_LOT_DATE", "生产日期不能晚于收货日", 422)
                expiry = datetime.combine(
                    line.expiry_date + timedelta(days=1), time.min, tzinfo=ZONE
                )
                params = {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "a": ctx.principal_id,
                    "s": source["sku_id"],
                    "m": sku["manufacturer_name"],
                    "n": line.lot_no,
                    "d": line.mfg_date,
                    "x": expiry,
                    "raw": line.expiry_date.isoformat(),
                }
                repo.execute(
                    """INSERT INTO med.lot(tenant_id,id,created_by,updated_by,sku_id,
                    manufacturer_key,lot_no,mfg_date,expiry_kind,expiry_exclusive,raw_expiry)
                    VALUES (:t,:id,:a,:a,:s,:m,:n,:d,'DATE',:x,:raw)
                    ON CONFLICT(tenant_id,sku_id,manufacturer_key,lot_no) DO NOTHING""",
                    params,
                )
                lot = one(
                    repo,
                    """SELECT * FROM med.lot WHERE sku_id=:s AND manufacturer_key=:m AND lot_no=:n
                    FOR UPDATE""",
                    params,
                )
                if (
                    lot["mfg_date"] != line.mfg_date
                    or lot["expiry_kind"] != "DATE"
                    or lot["expiry_exclusive"] != expiry
                ):
                    raise BusinessError(
                        "LOT_IDENTITY_CONFLICT", "同批号的生产日期或效期不一致，请核查", 409
                    )
                repo.execute(
                    """INSERT INTO med.receipt_line(tenant_id,id,receipt_id,order_line_id,sku_id,
                    lot_id,location_id,qty)
                    VALUES (:t,:id,:r,:ol,:s,:lot,:loc,:q)""",
                    {
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "r": rid,
                        "ol": source["id"],
                        "s": source["sku_id"],
                        "lot": lot["id"],
                        "loc": line.location_id,
                        "q": qty,
                    },
                )
            for lid, qty in totals.items():
                repo.execute(
                    "UPDATE med.purchase_order_line SET received_qty=received_qty+:q WHERE id=:id",
                    {"q": qty, "id": lid},
                )
            full = all(
                item["received_qty"] + totals.get(item["id"], Decimal(0)) == item["qty"]
                for item in lines
            )
            repo.execute(
                """UPDATE med.purchase_order SET version=version+1,fulfillment_state=:state
                WHERE id=:id""",
                {"id": order_id, "state": "RECEIVED" if full else "OPEN"},
            )
            audit(repo, ctx, "receipt", rid, "purchase.received", body.model_dump(mode="json"))
            return {
                "id": str(rid),
                "version": "1",
                "order_version": str(order["version"] + 1),
                "state": "PENDING_INSPECTION",
            }

        return self.run(ctx, "purchase.receive", order_id, body, key, execute)

    def inspect(
        self, ctx: TenantContext, receipt_id: UUID, body: InspectionCreate, key: str
    ) -> dict[str, Any]:
        order_id = self.receipt_order(ctx, receipt_id)
        self.scope(ctx, "purchase.inspect", order_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            has_qualified = any(Decimal(item.qualified_qty) > 0 for item in body.lines)
            order, _, skus = self.locked_order(
                repo, ctx, order_id, "purchase.inspect", validate=has_qualified
            )
            receipt = one(
                repo, "SELECT * FROM med.receipt WHERE id=:id FOR UPDATE", {"id": receipt_id}
            )
            version(receipt, body.expected_version)
            if receipt["created_by"] == ctx.principal_id:
                raise BusinessError("SEPARATION_REQUIRED", "收货人与验收人必须不同", 403)
            has_rejected = any(Decimal(item.rejected_qty) > 0 for item in body.lines)
            dictionary = (
                "inspection_mixed"
                if has_qualified and has_rejected
                else "inspection_rejected"
                if has_rejected
                else "inspection_qualified"
            )
            note = record_note(repo, ctx, dictionary, body.conclusion, body.note, 500)
            iid = uuid4()
            pid = uuid4() if has_qualified else None
            repo.execute(
                """INSERT INTO med.inspection(tenant_id,id,receipt_id,created_by,note)
                VALUES(:t,:id,:r,:a,:n)""",
                {
                    "t": ctx.tenant_id,
                    "id": iid,
                    "r": receipt_id,
                    "a": ctx.principal_id,
                    "n": note,
                },
            )
            for line in body.lines:
                row = one(
                    repo,
                    "SELECT * FROM med.receipt_line WHERE id=:id AND receipt_id=:r FOR UPDATE",
                    {"id": line.receipt_line_id, "r": receipt_id},
                )
                q, r = Decimal(line.qualified_qty), Decimal(line.rejected_qty)
                if q + r <= 0 or q + r > row["qty"] - row["qualified_qty"] - row["rejected_qty"]:
                    raise BusinessError(
                        "INSPECTION_EXCEEDS_RECEIPT", "验收数量必须大于零且不超过待验数量", 409
                    )
                try:
                    for amount in (q, r):
                        if amount:
                            quantity(
                                format(amount, "f"), sku_scale=skus[row["sku_id"]]["qty_scale"]
                            )
                except ValueError as exc:
                    raise BusinessError("INVALID_QUANTITY", "验收数量不符合商品精度", 422) from exc
                lot = one(
                    repo, "SELECT * FROM med.lot WHERE id=:id FOR SHARE", {"id": row["lot_id"]}
                )
                now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
                if q and (
                    lot["quality_blocked"]
                    or not lot["expiry_exclusive"]
                    or lot["expiry_exclusive"] <= now
                ):
                    raise BusinessError("LOT_INELIGIBLE", "冻结或过期批次不能判定为合格", 422)
                repo.execute(
                    """INSERT INTO med.inspection_line(tenant_id,id,inspection_id,receipt_line_id,
                    qualified_qty,rejected_qty)
                    VALUES(:t,:id,:i,:r,:q,:bad)""",
                    {"t": ctx.tenant_id, "id": uuid4(), "i": iid, "r": row["id"], "q": q, "bad": r},
                )
                repo.execute(
                    """UPDATE med.receipt_line SET qualified_qty=qualified_qty+:q,
                    rejected_qty=rejected_qty+:bad WHERE id=:id""",
                    {"id": row["id"], "q": q, "bad": r},
                )
            if pid:
                repo.execute(
                    "INSERT INTO med.receipt_posting(tenant_id,id,inspection_id) VALUES(:t,:id,:i)",
                    {"t": ctx.tenant_id, "id": pid, "i": iid},
                )
            repo.execute(
                "UPDATE med.receipt SET version=version+1 WHERE id=:id", {"id": receipt_id}
            )
            audit(
                repo,
                ctx,
                "inspection",
                iid,
                "purchase.inspected",
                {**body.model_dump(mode="json"), "note": note},
            )
            return {
                "inspection_id": str(iid),
                "receipt_posting_id": str(pid) if pid else None,
                "version": str(receipt["version"] + 1),
            }

        return self.run(ctx, "purchase.inspect", receipt_id, body, key, execute)

    def post(self, ctx: TenantContext, target: UUID, body: ReceiptPost, key: str) -> dict[str, Any]:
        from medinv.modules.procurement.application.posting import post_receipt

        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                """SELECT r.order_id FROM med.receipt_posting p
                JOIN med.inspection i ON (i.tenant_id,i.id)=(p.tenant_id,p.inspection_id)
                JOIN med.receipt r ON (r.tenant_id,r.id)=(i.tenant_id,i.receipt_id) WHERE
                p.id=:id""",
                {"id": target},
            )
        order_id = row["order_id"]
        self.scope(ctx, "purchase.post", order_id)
        return self.run(
            ctx,
            "purchase.post",
            target,
            body,
            key,
            lambda repo: post_receipt(self, repo, ctx, order_id, target, body),
        )

    def ledger(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int,
        *,
        search: str = "",
        state: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        ctx.require("inventory.read", entity)
        events = (
            "RECEIPT_QUALIFIED",
            "SALES_SHIPPED",
            "RETAIL_SOLD",
            "SALES_RETURNED",
            "RETAIL_RETURNED",
            "PURCHASE_RETURNED",
            "COUNT_GAIN",
            "COUNT_LOSS",
            "OPENING_BALANCE",
            "QUALITY_RELEASE",
            "QUALITY_WRITE_OFF",
            "TRANSFER_LOSS",
            "LOCATION_MOVE",
            "TRANSFER_DISPATCH",
            "TRANSFER_RECEIVE",
        )
        filters, params = document_filters(
            "sl",
            "concat_ws(' ',p.name,s.code,l.lot_no,w.name,loc.code,pl.posting_id::text)",
            {event: f"sl.event_type='{event}'" for event in events},
            search,
            state,
            date_from,
            date_to,
            date_column="posted_at",
        )
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT sl.id,sl.posted_at,sl.qty_delta,sl.event_type,p.name,s.base_unit,
                l.lot_no,w.name AS warehouse_name,loc.code AS location_code,
                pl.posting_id,cl.amount_delta,
                coalesce(pay.gross_amount,ar.gross_amount,-rd.gross_amount) AS gross_amount,
                coalesce(pay.tax_amount,ar.tax_amount,
                  -(rd.gross_amount-rd.net_amount)) AS tax_amount,
                CASE WHEN rd.kind='SALES' THEN 'SALES_RETURN'
                     WHEN rd.kind='PURCHASE' THEN 'PURCHASE_RETURN'
                     WHEN rd.kind='RETAIL' THEN 'RETAIL_RETURN'
                     WHEN ar.id IS NOT NULL THEN 'RECEIVABLE'
                     WHEN pay.id IS NOT NULL THEN 'PAYABLE' ELSE NULL END AS financial_kind,count(*)
                OVER() AS total
                FROM med.stock_ledger sl JOIN med.stock_balance b ON (b.tenant_id,
                b.id)=(sl.tenant_id,sl.balance_id)
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
                JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
                JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                LEFT JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                JOIN med.posting_line pl ON (pl.tenant_id,pl.id)=(sl.tenant_id,
                sl.posting_line_id)
                LEFT JOIN med.cost_ledger cl ON (cl.tenant_id,cl.posting_line_id,
                cl.effect_index)=(sl.tenant_id,sl.posting_line_id,sl.effect_index)
                LEFT JOIN med.payable_obligation pay ON (pay.tenant_id,
                pay.posting_id)=(pl.tenant_id,pl.posting_id)
                LEFT JOIN med.receivable_obligation ar ON (ar.tenant_id,
                ar.posting_id)=(pl.tenant_id,pl.posting_id)
                LEFT JOIN med.return_document rd
                ON (rd.tenant_id,rd.posting_id)=(pl.tenant_id,pl.posting_id)
                WHERE b.entity_id=:e {filters} ORDER BY sl.posted_at DESC,sl.id LIMIT 50 OFFSET
                :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }
