"""Read-only workbench projections. No parallel business facts or demo values."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from medinv.modules.procurement.application.service import ZONE, encode, one
from medinv.modules.workspace.application.queue import WorkKind, WorkQueue
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.tenancy.context import TenantContext

RISK_GROUPS = {
    "quality": ("质量处置", (WorkKind.QUALITY_REVIEW, WorkKind.QUALITY_POST)),
    "receipt": ("采购拒收交还", (WorkKind.PURCHASE_HANDBACK,)),
    "returns": ("退回品拒收交还", (WorkKind.RETURN_HANDBACK,)),
    "transit": ("在途损失", (WorkKind.TRANSFER_LOSS_REVIEW, WorkKind.TRANSFER_LOSS_POST)),
}
RISK_KINDS = frozenset(k for _, kinds in RISK_GROUPS.values() for k in kinds)


class WorkDashboard:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def period(self, ctx: TenantContext, entity: UUID, days: int) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        with self.uow.open(ctx, readonly=True, repeatable=True) as db:
            repo = SqlSession(db)
            one(repo, "SELECT id FROM med.legal_entity WHERE id=:e", {"e": entity})
            now = cast(datetime, one(repo, "SELECT clock_timestamp() AS now", {})["now"])
            today = now.astimezone(ZONE).date()
            start = today - timedelta(days=days - 1)
            previous = start - timedelta(days=days)
            params = {"e": entity, "start": previous, "end": today + timedelta(days=1)}
            # Each posting line and its cost effects are aggregated separately
            # before joining, so multiple allocations never multiply revenue.
            sales = repo.rows(
                """WITH costs AS (
                SELECT c.tenant_id,c.posting_line_id,sum(c.amount_delta) AS delta
                FROM med.cost_ledger c JOIN med.posting_line pl
                  ON (pl.tenant_id,pl.id)=(c.tenant_id,c.posting_line_id)
                JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(pl.tenant_id,pl.posting_id)
                WHERE pb.entity_id=:e AND pb.business_date>=:start AND pb.business_date<:end
                GROUP BY c.tenant_id,c.posting_line_id)
              SELECT b.business_date AS day,
                sum(CASE WHEN b.action IN ('SALES_RETURNED','RETAIL_RETURNED')
                  THEN -l.gross_amount ELSE l.gross_amount END) AS sales,
                sum(CASE WHEN b.action IN ('SALES_RETURNED','RETAIL_RETURNED')
                  THEN -l.net_amount ELSE l.net_amount END) AS net,
                sum(CASE WHEN b.action IN ('SALES_RETURNED','RETAIL_RETURNED')
                  THEN -l.net_amount ELSE l.net_amount END + coalesce(c.delta,0)) AS profit,
                count(*) FILTER (WHERE l.gross_amount IS NULL) AS missing_sales,
                count(*) FILTER (WHERE l.net_amount IS NULL) AS missing_net,
                count(*) FILTER (WHERE c.posting_line_id IS NULL
                  OR l.net_amount IS NULL) AS missing_cost
              FROM med.posting_batch b JOIN med.posting_line l
                ON (l.tenant_id,l.posting_id)=(b.tenant_id,b.id)
              LEFT JOIN costs c ON (c.tenant_id,c.posting_line_id)=(l.tenant_id,l.id)
              WHERE b.entity_id=:e AND b.business_date>=:start AND b.business_date<:end
                AND b.action IN ('SALES_SHIPPED','RETAIL_SOLD','SALES_RETURNED','RETAIL_RETURNED')
              GROUP BY b.business_date ORDER BY b.business_date""",
                params,
            )
            cash = repo.rows(
                """WITH cash AS (
              SELECT occurred_on AS day,amount FROM med.cash_document
                WHERE entity_id=:e AND direction='RECEIPT' AND state='CONFIRMED'
              UNION ALL SELECT f.occurred_on,-f.amount FROM med.cash_refund f
                JOIN med.return_document r ON (r.tenant_id,r.id)=(f.tenant_id,f.return_document_id)
                WHERE f.entity_id=:e AND f.direction='PAYMENT'
                  AND f.state='CONFIRMED' AND r.kind='SALES'
              UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date,
                CASE WHEN direction='RECEIPT' THEN amount ELSE -amount END
                FROM med.retail_payment WHERE entity_id=:e
              UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date,-amount
                FROM med.retail_return_refund WHERE entity_id=:e)
              SELECT day,sum(amount) AS cash FROM cash WHERE day>=:start AND day<:end
              GROUP BY day ORDER BY day""",
                params,
            )
            sm = {r["day"]: r for r in sales}
            cm = {r["day"]: r["cash"] for r in cash}
            points = []
            for i in range(days * 2):
                day = previous + timedelta(days=i)
                row = sm.get(day, {})
                points.append(
                    {
                        "day": day,
                        "sales": None if row.get("missing_sales") else row.get("sales", Decimal(0)),
                        "net": None if row.get("missing_net") else row.get("net", Decimal(0)),
                        "profit": None
                        if row.get("missing_cost")
                        else row.get("profit", Decimal(0)),
                        "cash": cm.get(day, Decimal(0)),
                    }
                )

            def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
                return {
                    key: None
                    if any(r[key] is None for r in rows)
                    else sum((r[key] for r in rows), Decimal(0))
                    for key in ("sales", "net", "profit", "cash")
                }

            return dict(
                encode(
                    {
                        "as_of": now,
                        "timezone": "Asia/Shanghai",
                        "start": start,
                        "end": today,
                        "days": days,
                        "current": totals(points[days:]),
                        "previous": totals(points[:days]),
                        "series": points[days:],
                    }
                )
            )

    def snapshot(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        with self.uow.open(ctx, readonly=True, repeatable=True) as db:
            repo = SqlSession(db)
            one(repo, "SELECT id FROM med.legal_entity WHERE id=:e", {"e": entity})
            now = cast(datetime, one(repo, "SELECT clock_timestamp() AS now", {})["now"])
            today = now.astimezone(ZONE).date()
            params = {"e": entity, "day": today, "now": now, "near": now + timedelta(days=90)}
            accounts = one(
                repo,
                """SELECT
              coalesce(sum(outstanding_amount) FILTER (WHERE kind='AR'),0) AS receivable,
              coalesce(sum(outstanding_amount) FILTER (WHERE kind='AP'),0) AS payable,
              coalesce(sum(outstanding_amount) FILTER
                (WHERE kind='AR' AND due_date>=:day),0) AS current,
              coalesce(sum(outstanding_amount) FILTER (WHERE kind='AR' AND due_date<:day
                AND due_date>=CAST(:day AS date)-30),0) AS late,
              coalesce(sum(outstanding_amount) FILTER
                (WHERE kind='AR' AND due_date<CAST(:day AS date)-30),0) AS overdue,
              count(DISTINCT party_id) FILTER
                (WHERE kind='AR' AND due_date<:day) AS overdue_customers
              FROM med.settlement_item WHERE entity_id=:e AND outstanding_amount>0""",
                params,
            )
            inventory = None
            if "inventory.read" in ctx.permissions:
                groups = repo.rows(
                    """SELECT p.category,sum(c.amount) AS amount,count(DISTINCT c.sku_id) AS skus
                  FROM med.cost_balance c JOIN med.sku s
                    ON (s.tenant_id,s.id)=(c.tenant_id,c.sku_id)
                  JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                  WHERE c.entity_id=:e AND c.qty>0 AND p.category<>'SERVICE'
                  GROUP BY p.category ORDER BY sum(c.amount) DESC,p.category""",
                    params,
                )
                expiry = one(
                    repo,
                    """SELECT
                  count(DISTINCT c.sku_id) FILTER
                    (WHERE l.expiry_exclusive>:now AND l.expiry_exclusive<=:near) AS near_skus,
                  coalesce(sum(c.amount) FILTER
                    (WHERE l.expiry_exclusive>:now AND l.expiry_exclusive<=:near),0) AS near_amount,
                  count(DISTINCT c.sku_id) FILTER (WHERE l.expiry_exclusive<=:now) AS expired_skus,
                  coalesce(sum(c.amount) FILTER
                    (WHERE l.expiry_exclusive<=:now),0) AS expired_amount
                  FROM med.cost_balance c JOIN med.lot l
                    ON (l.tenant_id,l.id)=(c.tenant_id,c.lot_id)
                  WHERE c.entity_id=:e AND c.qty>0""",
                    params,
                )
                inventory = {
                    "groups": groups,
                    "amount": sum((r["amount"] for r in groups), Decimal(0)),
                    **expiry,
                }
            return dict(
                encode(
                    {
                        "as_of": now,
                        "day": today,
                        "accounts": accounts,
                        "inventory": inventory,
                        "payable_due_status": "unavailable",
                        "slow_stock_status": "unavailable",
                    }
                )
            )

    def attention(self, ctx: TenantContext, entity: UUID, kind: str, offset: int) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        if kind != "overdue":
            ctx.require("inventory.read", entity)
        with self.uow.open(ctx, readonly=True, repeatable=True) as db:
            repo = SqlSession(db)
            one(repo, "SELECT id FROM med.legal_entity WHERE id=:e", {"e": entity})
            now = cast(datetime, one(repo, "SELECT clock_timestamp() AS now", {})["now"])
            today = now.astimezone(ZONE).date()
            params = {
                "e": entity,
                "day": today,
                "now": now,
                "near": now + timedelta(days=90),
                "offset": offset,
            }
            if kind == "overdue":
                source = """SELECT i.id,p.name AS name,sh.number AS number,i.due_date AS day,
                    i.outstanding_amount AS amount,o.id AS order_id,'sale' AS channel
                  FROM med.settlement_item i JOIN med.party p
                    ON (p.tenant_id,p.id)=(i.tenant_id,i.party_id)
                  JOIN med.receivable_obligation ar ON (ar.tenant_id,ar.id)=(i.tenant_id,i.id)
                  JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(ar.tenant_id,ar.shipment_id)
                  JOIN med.sales_order o ON (o.tenant_id,o.id)=(sh.tenant_id,sh.order_id)
                  WHERE i.entity_id=:e AND i.kind='AR'
                    AND i.outstanding_amount>0 AND i.due_date<:day"""
            else:
                condition = (
                    "l.expiry_exclusive<=:now"
                    if kind == "expired"
                    else "l.expiry_exclusive>:now AND l.expiry_exclusive<=:near"
                )
                source = f"""SELECT c.id,p.name,s.code AS number,
                    (l.expiry_exclusive AT TIME ZONE 'Asia/Shanghai')::date-1 AS day,c.amount,
                    l.lot_no,c.qty,s.base_unit,NULL::uuid AS order_id,NULL::text AS channel
                  FROM med.cost_balance c JOIN med.lot l
                    ON (l.tenant_id,l.id)=(c.tenant_id,c.lot_id)
                  JOIN med.sku s ON (s.tenant_id,s.id)=(c.tenant_id,c.sku_id)
                  JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                  WHERE c.entity_id=:e AND c.qty>0 AND {condition}"""
            result = one(
                repo,
                f"""WITH items AS MATERIALIZED ({source}), page AS (
                SELECT * FROM items ORDER BY day,id LIMIT 20 OFFSET :offset)
                SELECT (SELECT count(*) FROM items) AS total,
                  (SELECT coalesce(sum(amount),0) FROM items) AS amount,
                  coalesce((SELECT jsonb_agg(to_jsonb(page)||jsonb_build_object(
                    'amount',page.amount::text,'qty',to_jsonb(page)->>'qty'))
                    FROM page),'[]'::jsonb) AS items""",
                params,
            )
            if isinstance(result["items"], str):
                result["items"] = json.loads(result["items"])
            return dict(
                encode(
                    {
                        **result,
                        "as_of": now,
                        "has_more": offset + len(result["items"]) < result["total"],
                    }
                )
            )

    def risks(
        self,
        ctx: TenantContext,
        entity: UUID,
        kind: WorkKind,
        search: str,
        offset: int,
        group: str = "all",
    ) -> dict[str, Any]:
        allowed = RISK_KINDS if group == "all" else frozenset(RISK_GROUPS[group][1])
        result = WorkQueue(self.uow).listing(
            ctx, entity, kind, search, offset, limit=20, allowed=allowed
        )
        visible = {k["code"] for k in result["kinds"]}
        result["groups"] = [
            {
                "code": code,
                "name": name,
                "count": sum(result["counts"].get(k.value, 0) for k in kinds),
                "kinds": [k.value for k in kinds if k.value in visible],
            }
            for code, (name, kinds) in RISK_GROUPS.items()
            if any(k.value in visible for k in kinds)
        ]
        result["total_open"] = sum(result["counts"].values())
        result["history_status"] = "unavailable"
        result["assignment_status"] = "unavailable"
        return result
