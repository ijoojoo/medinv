"""Read-only purchasing worklists, derived from existing business facts.

No synthetic workflow state or stock mutation lives in this projection. Quantities
are grouped by base unit so boxes and pieces are never summed into one number.
"""

from datetime import date
from typing import Any
from uuid import UUID

from medinv.modules.procurement.application.service import encode
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def worklist(
    uow: TenantUnitOfWork,
    ctx: TenantContext,
    entity: UUID,
    stage: str,
    view: str = "pending",
    offset: int = 0,
    search: str = "",
    state: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_id: UUID | None = None,
    party_id: UUID | None = None,
    sort: str = "newest",
) -> dict[str, Any]:
    ctx.require("purchase.read", entity)
    if (
        stage not in {"orders", "receiving", "inspection", "posting"}
        or view not in {"pending", "history"}
        or sort not in {"newest", "oldest"}
    ):
        raise BusinessError("INVALID_QUERY", "采购环节或查询方式无效", 422)
    receipt_id = inspection_id = segment_id = "NULL::uuid"
    receipt_number = "''::text"
    joins = ""
    condition = "TRUE"
    action = "''::text"
    actor = "NULL::uuid"
    permission = ""
    metrics: dict[str, str]
    if stage == "orders" or (stage == "receiving" and view == "pending"):
        identifier, number, occurred = "o.id", "o.number", "o.created_at"
        source = "med.purchase_order_line l"
        link = "l.order_id=o.id"
        metrics = {
            "ordered": "l.qty",
            "received": "l.received_qty",
            "pending": "l.qty-l.received_qty-l.closed_qty",
            "closed": "l.closed_qty",
        }
        status = (
            "CASE WHEN o.approval_state='APPROVED' THEN o.fulfillment_state "
            "ELSE o.approval_state END"
        )
        states = ["DRAFT", "OPEN", "RECEIVED", "CLOSED", "CANCELLED"]
        if stage == "receiving":
            condition = "o.approval_state='APPROVED' AND o.fulfillment_state='OPEN' AND q.pending>0"
            status = "CASE WHEN q.processed>0 THEN 'PARTIAL' ELSE 'WAITING' END"
            states = ["WAITING", "PARTIAL"]
            action, permission = "'receive'::text", "purchase.receive"
        pending, processed = metrics["pending"], metrics["received"]
    elif stage == "receiving" or (stage == "inspection" and view == "pending"):
        joins = "JOIN med.receipt r ON (r.tenant_id,r.order_id)=(o.tenant_id,o.id)"
        identifier, number, occurred = "r.id", "r.number", "r.received_at"
        receipt_id, receipt_number = "r.id", "r.number"
        source, link = "med.receipt_line l", "l.receipt_id=r.id"
        metrics = {
            "received": "l.qty",
            "pending": "l.qty-l.qualified_qty-l.rejected_qty",
            "qualified": "l.qualified_qty",
            "rejected": "l.rejected_qty",
            "posted": "l.posted_qty",
        }
        pending, processed = metrics["pending"], "l.qualified_qty+l.rejected_qty"
        status = (
            "CASE WHEN q.pending=0 THEN 'DONE' WHEN q.processed>0 THEN 'PARTIAL' ELSE 'WAITING' END"
        )
        states = ["WAITING", "PARTIAL", "DONE"]
        if stage == "inspection":
            condition = "q.pending>0"
            action, permission, actor = "'inspect'::text", "purchase.inspect", "r.created_by"
            states = ["WAITING", "PARTIAL"]
    else:
        joins = """JOIN med.receipt r ON (r.tenant_id,r.order_id)=(o.tenant_id,o.id)
            JOIN med.inspection i ON (i.tenant_id,i.receipt_id)=(r.tenant_id,r.id)"""
        identifier, number, occurred = "i.id", "r.number", "i.inspected_at"
        receipt_id, receipt_number, inspection_id = "r.id", "r.number", "i.id"
        source = """med.inspection_line il JOIN med.receipt_line l
            ON (l.tenant_id,l.id)=(il.tenant_id,il.receipt_line_id)"""
        link = "il.inspection_id=i.id"
        metrics = {"qualified": "il.qualified_qty", "rejected": "il.rejected_qty"}
        pending, processed = "0", "il.qualified_qty+il.rejected_qty"
        status, states = "'RECORDED'::text", ["RECORDED"]
        if stage == "posting":
            joins += """ JOIN med.receipt_posting rp
                ON (rp.tenant_id,rp.inspection_id)=(i.tenant_id,i.id)
                LEFT JOIN med.posting_batch b
                ON (b.tenant_id,b.id)=(rp.tenant_id,rp.posting_id)"""
            identifier, segment_id = "rp.id", "rp.id"
            status = "rp.state"
            if view == "pending":
                condition, states = "rp.state='APPROVED'", ["APPROVED"]
                action, permission = "'post'::text", "purchase.post"
            else:
                condition, states, occurred = "rp.state='POSTED'", ["POSTED"], "b.posted_at"
            metrics = {"qualified": "il.qualified_qty"}
    sums = ",".join(f"sum({expr}) AS {key}" for key, expr in metrics.items())
    json_fields = ",".join(f"'{key}',g.{key}::text" for key in metrics)
    filters, params = document_filters(
        "v",
        "concat_ws(' ',v.number,v.order_number,v.supplier_name,v.supplier_code)",
        {s: f"v.state='{s}'" for s in states},
        search,
        state,
        date_from,
        date_to,
    )
    if warehouse_id:
        filters += " AND v.warehouse_id=:warehouse"
        params["warehouse"] = warehouse_id
    if party_id:
        filters += " AND v.supplier_id=:party"
        params["party"] = party_id
    direction = "ASC" if sort == "oldest" else "DESC"
    with uow.open(ctx, readonly=True) as session:
        rows = SqlSession(session).rows(
            f"""WITH records AS (
              SELECT {identifier} AS id,{number} AS number,o.id AS order_id,
                o.number AS order_number,{receipt_id} AS receipt_id,
                {receipt_number} AS receipt_number,{inspection_id} AS inspection_id,
                {segment_id} AS segment_id,{occurred} AS created_at,
                p.name AS supplier_name,p.code AS supplier_code,o.supplier_id,o.warehouse_id,
                o.approval_state,o.fulfillment_state,
                w.name AS warehouse_name,{status} AS state,q.quantities,
                CASE WHEN :allowed AND ({actor} IS NULL OR {actor}<>:actor)
                  THEN {action} ELSE '' END AS action,
                CASE WHEN {actor}=:actor THEN '请由另一位成员验收' ELSE '' END AS action_note
              FROM med.purchase_order o
              JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
              JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
              {joins}
              JOIN LATERAL (
                SELECT jsonb_agg(jsonb_build_object('unit',g.base_unit,{json_fields})
                    ORDER BY g.base_unit) AS quantities,
                    sum(g.pending_total) AS pending,sum(g.processed_total) AS processed
                FROM (SELECT s.base_unit,{sums},sum({pending}) AS pending_total,
                    sum({processed}) AS processed_total
                  FROM {source}
                  JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
                  WHERE l.tenant_id=o.tenant_id AND {link} GROUP BY s.base_unit) g
              ) q ON TRUE
              WHERE o.entity_id=:e AND ({condition})
            ) SELECT v.* FROM records v WHERE TRUE {filters}
              ORDER BY v.created_at {direction},v.id {direction} LIMIT 51 OFFSET :offset""",
            {
                "e": entity,
                "offset": offset,
                "allowed": permission in ctx.permissions,
                "actor": ctx.principal_id,
                **params,
            },
        )
    return {"items": encode(rows[:50]), "has_more": len(rows) > 50}
