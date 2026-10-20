import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from medinv.modules.procurement.application.service import encode, one
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class WorkKind(StrEnum):
    ALL = "all"
    PURCHASE_APPROVE = "purchase_approve"
    PURCHASE_RECEIVE = "purchase_receive"
    PURCHASE_INSPECT = "purchase_inspect"
    PURCHASE_POST = "purchase_post"
    PURCHASE_HANDBACK = "purchase_handback"
    SALES_APPROVE = "sales_approve"
    SALES_ALLOCATE = "sales_allocate"
    SALES_PICK = "sales_pick"
    SALES_VERIFY = "sales_verify"
    SALES_POST = "sales_post"
    RETAIL_PAY = "retail_pay"
    RETAIL_COMPLETE = "retail_complete"
    PURCHASE_RETURN_REVIEW = "purchase_return_review"
    PURCHASE_RETURN_POST = "purchase_return_post"
    RETURN_RECEIVE = "return_receive"
    RETURN_REVIEW = "return_review"
    RETURN_POST = "return_post"
    RETURN_HANDBACK = "return_handback"
    RETURN_REFUND_CHECK = "return_refund_check"
    RETAIL_RETURN_REFUND = "retail_return_refund"
    TRANSFER_REVIEW = "transfer_review"
    TRANSFER_DISPATCH = "transfer_dispatch"
    TRANSFER_RECEIVE = "transfer_receive"
    COUNT_ENTER = "count_enter"
    COUNT_REVIEW = "count_review"
    COUNT_POST = "count_post"
    TRANSFER_LOSS_REVIEW = "transfer_loss_review"
    TRANSFER_LOSS_POST = "transfer_loss_post"
    QUALITY_REVIEW = "quality_review"
    QUALITY_POST = "quality_post"
    OPENING_REVIEW = "opening_review"
    OPENING_POST = "opening_post"


@dataclass(frozen=True)
class QueueQuery:
    label: str
    page: str
    permission: str
    sql: str
    additional_permission: str | None = None


def purchase(
    label: str,
    predicate: str,
    join: str = "",
    item: str = "o.id",
    related: str = "NULL::varchar",
    moment: str = "o.created_at",
) -> QueueQuery:
    return QueueQuery(
        label,
        "purchasing",
        "purchase.read",
        f"""
        SELECT {item} AS item_id,o.id AS target_id,o.number,{related} AS related_number,
        p.name AS party_name,w.name AS warehouse_name,{moment} AS document_time,
        false AS reservation_expired
        FROM med.purchase_order o JOIN med.party p
        ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
        {join} WHERE o.entity_id=:e AND ({predicate})""",
    )


def sales(label: str, predicate: str, shipment: bool = False) -> QueueQuery:
    item, related, moment, expired, join = "o.id", "NULL::varchar", "o.created_at", "false", ""
    if shipment:
        item, related, moment = "s.id", "s.number", "s.created_at"
        expired = "(s.state='ALLOCATED' AND r.expires_at<=clock_timestamp())"
        join = """JOIN med.shipment s ON (s.tenant_id,s.order_id)=(o.tenant_id,o.id)
            JOIN med.reservation r ON (r.tenant_id,r.id)=(s.tenant_id,s.reservation_id)"""
    return QueueQuery(
        label,
        "selling",
        "sales.read",
        f"""
        SELECT {item} AS item_id,o.id AS target_id,o.number,{related} AS related_number,
        p.name AS party_name,w.name AS warehouse_name,{moment} AS document_time,
        {expired} AS reservation_expired
        FROM med.sales_order o JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.customer_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
        {join} WHERE o.entity_id=:e AND ({predicate})""",
    )


def retail(label: str, state: str) -> QueueQuery:
    return QueueQuery(
        label,
        "retail",
        "retail.read",
        f"""
        SELECT o.id AS item_id,o.id AS target_id,o.number,NULL::varchar AS related_number,
        '散客零售' AS party_name,w.name AS warehouse_name,o.created_at AS document_time,
        (o.state='RESERVED' AND r.expires_at<=clock_timestamp()) AS reservation_expired
        FROM med.retail_sale o JOIN med.warehouse w
        ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
        JOIN med.reservation r ON (r.tenant_id,r.id)=(o.tenant_id,o.reservation_id)
        WHERE o.entity_id=:e AND o.state='{state}'""",
    )


def returns(label: str, predicate: str, financial: bool = False) -> QueueQuery:
    return QueueQuery(
        label,
        "returns",
        "returns.read",
        f"""
        SELECT r.id AS item_id,r.id AS target_id,r.number,
        coalesce(sh.number,rc.number,pos.number) AS related_number,
        coalesce(p.name,'零售顾客') AS party_name,w.name AS warehouse_name,
        r.created_at AS document_time,false AS reservation_expired
        FROM med.return_document r
        JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(r.tenant_id,r.source_posting_id)
        LEFT JOIN med.party p ON (p.tenant_id,p.id)=(r.tenant_id,r.party_id)
        LEFT JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(pb.tenant_id,pb.source_id)
          AND r.kind='SALES'
        LEFT JOIN med.sales_order so ON (so.tenant_id,so.id)=(sh.tenant_id,sh.order_id)
        LEFT JOIN med.receipt_posting rp ON (rp.tenant_id,rp.id)=(pb.tenant_id,pb.source_id)
          AND r.kind='PURCHASE'
        LEFT JOIN med.inspection ins ON (ins.tenant_id,ins.id)=(rp.tenant_id,rp.inspection_id)
        LEFT JOIN med.receipt rc ON (rc.tenant_id,rc.id)=(ins.tenant_id,ins.receipt_id)
        LEFT JOIN med.purchase_order po ON (po.tenant_id,po.id)=(rc.tenant_id,rc.order_id)
        LEFT JOIN med.retail_sale pos ON (pos.tenant_id,pos.id)=(r.tenant_id,r.retail_sale_id)
        JOIN med.warehouse w ON w.tenant_id=r.tenant_id
          AND w.id=coalesce(so.warehouse_id,po.warehouse_id,pos.warehouse_id)
        WHERE r.entity_id=:e AND ({predicate})""",
        "finance.read" if financial else None,
    )


def transfer(label: str, predicate: str) -> QueueQuery:
    return QueueQuery(
        label,
        "transfers",
        "transfers.read",
        f"""
        SELECT d.id AS item_id,d.id AS target_id,d.number,NULL::varchar AS related_number,
        '内部调拨' AS party_name,s.name || ' → ' || w.name AS warehouse_name,
        d.created_at AS document_time,false AS reservation_expired
        FROM med.transfer d
        JOIN med.warehouse s ON (s.tenant_id,s.id)=(d.tenant_id,d.source_warehouse_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.destination_warehouse_id)
        WHERE d.entity_id=:e AND ({predicate})""",
    )


def stock_count(label: str, state: str) -> QueueQuery:
    return QueueQuery(
        label,
        "counts",
        "counts.read",
        f"""
        SELECT d.id AS item_id,d.id AS target_id,d.number,NULL::varchar AS related_number,
        '库存盘点' AS party_name,w.name AS warehouse_name,
        d.created_at AS document_time,false AS reservation_expired
        FROM med.stock_count d
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
        WHERE d.entity_id=:e AND d.state='{state}'""",
    )


def inventory_opening(label: str, state: str) -> QueueQuery:
    return QueueQuery(
        label,
        "openings",
        "inventory.read",
        f"""
        SELECT d.id AS item_id,d.id AS target_id,d.number,d.source_ref AS related_number,
        '期初库存' AS party_name,w.name AS warehouse_name,
        d.created_at AS document_time,false AS reservation_expired
        FROM med.inventory_opening d JOIN med.warehouse w
          ON (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
        WHERE d.entity_id=:e AND d.state='{state}'""",
    )


def transfer_loss(label: str, state: str) -> QueueQuery:
    return QueueQuery(
        label,
        "transfers",
        "transfers.read",
        f"""
        SELECT l.id AS item_id,d.id AS target_id,d.number,l.number AS related_number,
        '运输短少核查' AS party_name,s.name || ' → ' || w.name AS warehouse_name,
        l.created_at AS document_time,false AS reservation_expired
        FROM med.transfer_loss l JOIN med.transfer d
          ON (d.tenant_id,d.id)=(l.tenant_id,l.transfer_id)
        JOIN med.warehouse s ON (s.tenant_id,s.id)=(d.tenant_id,d.source_warehouse_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.destination_warehouse_id)
        WHERE d.entity_id=:e AND l.state='{state}'""",
    )


def quality_disposition(label: str, state: str) -> QueueQuery:
    return QueueQuery(
        label,
        "quality",
        "quality.read",
        f"""
        SELECT d.id AS item_id,d.id AS target_id,d.number,NULL::varchar AS related_number,
        CASE WHEN d.kind='RELEASE' THEN '隔离品放行' ELSE '隔离品报损' END AS party_name,
        w.name AS warehouse_name,d.created_at AS document_time,false AS reservation_expired
        FROM med.quality_disposition d JOIN med.quality_disposition_item i
          ON (i.tenant_id,i.disposition_id)=(d.tenant_id,d.id)
        JOIN med.stock_balance b ON (b.tenant_id,b.id)=(i.tenant_id,i.balance_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
        WHERE d.entity_id=:e AND d.state='{state}'""",
    )


RECEIPT = "JOIN med.receipt r ON (r.tenant_id,r.order_id)=(o.tenant_id,o.id)"
# Only these static fragments are interpolated; all user input remains bound parameters.
QUERIES = {
    WorkKind.PURCHASE_APPROVE: purchase("采购待审核", "o.approval_state='DRAFT'"),
    WorkKind.PURCHASE_RECEIVE: purchase(
        "采购待收货",
        """o.approval_state='APPROVED'
        AND o.fulfillment_state='OPEN' AND EXISTS (SELECT 1 FROM med.purchase_order_line l
        WHERE (l.tenant_id,l.order_id)=(o.tenant_id,o.id)
        AND l.qty>l.received_qty+l.closed_qty)""",
    ),
    WorkKind.PURCHASE_INSPECT: purchase(
        "到货待验收",
        """EXISTS (
        SELECT 1 FROM med.receipt_line l WHERE (l.tenant_id,l.receipt_id)=(r.tenant_id,r.id)
        AND l.qty>l.qualified_qty+l.rejected_qty)""",
        RECEIPT,
        "r.id",
        "r.number",
        "r.received_at",
    ),
    WorkKind.PURCHASE_POST: purchase(
        "合格待入库",
        "rp.state='APPROVED'",
        RECEIPT
        + """
        JOIN med.inspection i ON (i.tenant_id,i.receipt_id)=(r.tenant_id,r.id)
        JOIN med.receipt_posting rp ON (rp.tenant_id,rp.inspection_id)=(i.tenant_id,i.id)""",
        "rp.id",
        "r.number",
        "i.inspected_at",
    ),
    WorkKind.PURCHASE_HANDBACK: purchase(
        "拒收待退回",
        """EXISTS (
        SELECT 1 FROM med.receipt_line l WHERE (l.tenant_id,l.receipt_id)=(r.tenant_id,r.id)
        AND l.rejected_qty>l.rejected_returned_qty)""",
        RECEIPT,
        "r.id",
        "r.number",
        "r.received_at",
    ),
    WorkKind.SALES_APPROVE: sales("销售待审核", "o.state='DRAFT'"),
    WorkKind.SALES_ALLOCATE: sales(
        "销售待分配",
        """o.state='APPROVED' AND EXISTS (
        SELECT 1 FROM med.inventory_demand_line l
        WHERE (l.tenant_id,l.demand_id)=(o.tenant_id,o.id)
        AND l.requested_qty>l.reserved_qty+l.fulfilled_qty)""",
    ),
    WorkKind.SALES_PICK: sales("发货待拣货", "s.state='ALLOCATED'", True),
    WorkKind.SALES_VERIFY: sales("发货待复核", "s.state='PICKED'", True),
    WorkKind.SALES_POST: sales("销售待出库", "s.state='VERIFIED'", True),
    WorkKind.RETAIL_PAY: retail("零售待收款", "RESERVED"),
    WorkKind.RETAIL_COMPLETE: retail("零售已收款待出库", "PAID_PENDING_POST"),
    WorkKind.PURCHASE_RETURN_REVIEW: returns(
        "采购退货待审核", "r.kind='PURCHASE' AND r.state='DRAFT'"
    ),
    WorkKind.PURCHASE_RETURN_POST: returns(
        "采购退货待出库", "r.kind='PURCHASE' AND r.state='APPROVED'"
    ),
    WorkKind.RETURN_RECEIVE: returns(
        "退回品待收货",
        "r.kind IN ('SALES','RETAIL') AND r.workflow_version=2 AND r.state='DRAFT'",
    ),
    WorkKind.RETURN_REVIEW: returns(
        "退回品待验收",
        """r.kind IN ('SALES','RETAIL') AND
        ((r.workflow_version=2 AND r.state='RECEIVED')
         OR (r.workflow_version=1 AND r.state='DRAFT'))""",
    ),
    WorkKind.RETURN_POST: returns(
        "退回品待入库", "r.kind IN ('SALES','RETAIL') AND r.state='APPROVED'"
    ),
    WorkKind.RETURN_HANDBACK: returns(
        "退回品待交还",
        """r.kind IN ('SALES','RETAIL') AND r.state='REJECTED'
        AND EXISTS (SELECT 1 FROM med.return_custody c
          WHERE (c.tenant_id,c.return_id)=(r.tenant_id,r.id))
        AND NOT EXISTS (SELECT 1 FROM med.return_custody_close c
          WHERE (c.tenant_id,c.return_id)=(r.tenant_id,r.id))""",
    ),
    WorkKind.RETURN_REFUND_CHECK: returns(
        "退货退款待核对",
        """r.kind IN ('SALES','PURCHASE') AND r.state='POSTED'
        AND r.gross_amount>(SELECT coalesce(sum(f.amount),0) FROM med.cash_refund f
          WHERE (f.tenant_id,f.return_document_id)=(r.tenant_id,r.id) AND f.state='CONFIRMED')
        AND EXISTS (SELECT 1 FROM med.cash_document c
          WHERE c.tenant_id=r.tenant_id AND c.entity_id=r.entity_id AND c.party_id=r.party_id
          AND c.state='CONFIRMED' AND c.amount>c.allocated_amount+c.refunded_amount
          AND c.direction=CASE WHEN r.kind='SALES' THEN 'RECEIPT' ELSE 'PAYMENT' END
          AND EXISTS (SELECT 1 FROM med.settlement_allocation a
            WHERE (a.tenant_id,a.cash_document_id)=(c.tenant_id,c.id)
            AND coalesce(a.receivable_id,a.payable_id)=coalesce(r.receivable_id,r.payable_id)))""",
        financial=True,
    ),
    WorkKind.RETAIL_RETURN_REFUND: returns(
        "零售退货待退款",
        """r.kind='RETAIL' AND r.state='POSTED' AND r.gross_amount>0
        AND NOT EXISTS (SELECT 1 FROM med.retail_return_refund f
          WHERE (f.tenant_id,f.return_id)=(r.tenant_id,r.id))""",
    ),
    WorkKind.TRANSFER_REVIEW: transfer("调拨待审核", "d.state='DRAFT'"),
    WorkKind.TRANSFER_DISPATCH: transfer("调拨待发出", "d.state='APPROVED'"),
    WorkKind.TRANSFER_RECEIVE: transfer("调拨待收货", "d.state IN ('IN_TRANSIT','PART_RECEIVED')"),
    WorkKind.COUNT_ENTER: stock_count("盘点待实盘", "COUNTING"),
    WorkKind.COUNT_REVIEW: stock_count("盘点待审核", "SUBMITTED"),
    WorkKind.COUNT_POST: stock_count("盘点待过账", "APPROVED"),
    # Pending loss claims remain visible even after delayed goods close the parent transfer.
    WorkKind.TRANSFER_LOSS_REVIEW: transfer_loss("在途损失待审核", "DRAFT"),
    WorkKind.TRANSFER_LOSS_POST: transfer_loss("在途损失待核销", "APPROVED"),
    WorkKind.QUALITY_REVIEW: quality_disposition("质量处置待审核", "DRAFT"),
    WorkKind.QUALITY_POST: quality_disposition("质量处置待过账", "APPROVED"),
    WorkKind.OPENING_REVIEW: inventory_opening("期初待审核", "DRAFT"),
    WorkKind.OPENING_POST: inventory_opening("期初待启用", "APPROVED"),
}


class WorkQueue:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(
        self,
        ctx: TenantContext,
        entity: UUID,
        kind: WorkKind,
        search: str,
        offset: int,
        *,
        limit: int = 50,
        priority: bool = False,
        allowed: frozenset[WorkKind] | None = None,
    ) -> dict[str, Any]:
        visible = {
            k: q
            for k, q in QUERIES.items()
            if q.permission in ctx.permissions
            and (q.additional_permission is None or q.additional_permission in ctx.permissions)
            and (allowed is None or k in allowed)
        }
        if not visible:
            raise BusinessError("FORBIDDEN", "没有读取进销存待办的权限", 403)
        ctx.require(next(iter(visible.values())).permission, entity)
        if kind != WorkKind.ALL:
            if kind not in visible:
                raise BusinessError("FORBIDDEN", "该环节不在当前可访问范围", 403)
            ctx.require(QUERIES[kind].permission, entity)
            if additional := QUERIES[kind].additional_permission:
                ctx.require(additional, entity)
        # A single SQL statement gives pagination a common snapshot across all work stages.
        union = " UNION ALL ".join(
            f"SELECT '{k.value}' AS kind,'{q.page}' AS page,task.* FROM ({q.sql}) task"
            for k, q in visible.items()
        )
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            # A pooled prepared plan estimated for a small tenant can time out on
            # a larger one under RLS. Replan this interactive union for the current
            # tenant/search; SET LOCAL cannot affect later users of the connection.
            repo.execute("SET LOCAL plan_cache_mode = force_custom_plan", {})
            one(repo, "SELECT id FROM med.legal_entity WHERE id=:e", {"e": entity})
            result = one(
                repo,
                f"""WITH work AS MATERIALIZED ({union}), filtered AS MATERIALIZED (
                SELECT * FROM work WHERE (:q='' OR strpos(lower(number),lower(:q))>0
                  OR strpos(lower(coalesce(related_number,'')),lower(:q))>0
                  OR strpos(lower(party_name),lower(:q))>0
                  OR strpos(lower(warehouse_name),lower(:q))>0)
                  AND (NOT :priority OR reservation_expired)), selected AS MATERIALIZED (
                SELECT * FROM filtered WHERE :kind='all' OR kind=:kind), page AS (
                SELECT * FROM selected
                  ORDER BY reservation_expired DESC,document_time,kind,item_id
                LIMIT :limit OFFSET :offset)
                SELECT coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM page p),'[]'::jsonb) AS items,
                (SELECT count(*) FROM selected) AS total,
                (SELECT count(*) FROM filtered WHERE reservation_expired) AS priority_count,
                coalesce((SELECT jsonb_object_agg(kind,n) FROM
                  (SELECT kind,count(*) AS n FROM filtered GROUP BY kind) c),
                  '{{}}'::jsonb) AS counts,
                clock_timestamp() AS as_of""",
                {
                    "e": entity,
                    "q": search.strip(),
                    "offset": offset,
                    "limit": limit,
                    "kind": kind.value,
                    "priority": priority,
                },
            )
            for field in ("items", "counts"):
                if isinstance(result[field], str):
                    result[field] = json.loads(result[field])
            return dict(
                encode(
                    {
                        **result,
                        "items": [{**row, "entity_id": entity} for row in result["items"]],
                        "has_more": offset + len(result["items"]) < result["total"],
                        "kinds": [{"code": k.value, "name": q.label} for k, q in visible.items()],
                    }
                )
            )
