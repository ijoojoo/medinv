import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.counts import CountCreate, CountReview, CountSubmit
from medinv.modules.inventory.domain.decimal_values import portion, quantity
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

BALANCES = """SELECT b.*,c.id AS cost_balance_id,c.version AS cost_version,
  c.qty AS pool_qty,c.amount AS pool_amount,s.code AS sku_code,s.base_unit,s.qty_scale,
  s.serial_required,p.name,p.specification,p.manufacturer_name,p.approval_number,p.category,
  p.restricted_category,p.approval_state,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current,
  l.lot_no,l.expiry_exclusive,l.quality_blocked,g.blocked AS gate_blocked,
  loc.code AS location_code
  FROM med.stock_balance b JOIN med.cost_balance c
    ON (c.tenant_id,c.entity_id,c.owner_id,c.sku_id,c.lot_id)=
       (b.tenant_id,b.entity_id,b.owner_id,b.sku_id,b.lot_id)
  JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
  JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
  JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
  JOIN med.stock_gate g ON (g.tenant_id,g.entity_id,g.sku_id)=
    (b.tenant_id,b.entity_id,b.sku_id)
  JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
"""


class CountService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    def options(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("counts.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                "SELECT id,name FROM med.warehouse WHERE entity_id=:e AND kind='PHYSICAL' "
                "AND archived_at IS NULL "
                "ORDER BY name,id LIMIT 201",
                {"e": entity},
            )
            if len(rows) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "仓库超过当前选项上限", 422)
            return {"warehouses": encode(rows)}

    def candidates(
        self, ctx: TenantContext, entity: UUID, warehouse: UUID, search: str, offset: int
    ) -> dict[str, Any]:
        ctx.require("counts.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                f"""SELECT q.*,count(*) OVER() AS total FROM ({BALANCES}) q
                WHERE entity_id=:e AND warehouse_id=:w AND quality_status='QUALIFIED'
                  AND NOT serial_required AND restricted_category IS NULL
                  AND (name ILIKE :search OR sku_code ILIKE :search OR lot_no ILIKE :search)
                ORDER BY sku_code,lot_no,location_code,id LIMIT 50 OFFSET :offset""",
                {"e": entity, "w": warehouse, "search": "%" + search + "%", "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def count_scope(self, ctx: TenantContext, cid: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            entity = one(
                SqlSession(session),
                "SELECT entity_id FROM med.stock_count WHERE id=:id",
                {"id": cid},
            )["entity_id"]
        ctx.require(permission, entity)
        return UUID(str(entity))

    def listing(
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
        ctx.require("counts.read", entity)
        filters, params = document_filters(
            "d",
            "concat_ws(' ',d.number,w.name,d.reason)",
            {
                "COUNTING": "d.state='COUNTING'",
                "SUBMITTED": "d.state='SUBMITTED'",
                "APPROVED": "d.state='APPROVED'",
                "POSTED": "d.state='POSTED'",
                "CANCELLED": "d.state='CANCELLED'",
            },
            search,
            state,
            date_from,
            date_to,
        )
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT d.*,w.name AS warehouse_name,count(*) OVER() AS total
                FROM med.stock_count d JOIN med.warehouse w
                ON (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
                WHERE d.entity_id=:e {filters}
                ORDER BY d.created_at DESC,d.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def lines(self, repo: SqlSession, cid: UUID) -> list[dict[str, Any]]:
        return repo.rows(
            """SELECT l.*,b.sku_id,b.lot_id,b.location_id,lot.lot_no,loc.code AS location_code,
            l.product_snapshot->>'name' AS name,l.package_snapshot->>'base_unit' AS base_unit,
            (l.package_snapshot->>'qty_scale')::int AS qty_scale,
            m.actual_qty,m.qty_delta,m.cost_delta,m.gain_unit_cost,m.note AS count_note
            FROM med.stock_count_line l JOIN med.stock_balance b
              ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id)
            JOIN med.lot lot ON (lot.tenant_id,lot.id)=(b.tenant_id,b.lot_id)
            JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
            LEFT JOIN med.stock_count_measurement m ON (m.tenant_id,m.line_id)=(l.tenant_id,l.id)
            WHERE l.count_id=:id ORDER BY l.line_no""",
            {"id": cid},
        )

    def detail(self, ctx: TenantContext, cid: UUID) -> dict[str, Any]:
        self.count_scope(ctx, cid, "counts.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            doc = one(
                repo,
                """SELECT d.*,w.name AS warehouse_name FROM med.stock_count d
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
                WHERE d.id=:id""",
                {"id": cid},
            )
            doc["version"] = str(doc["version"])
            result = None
            if doc["posting_id"]:
                result = one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["posting_id"]},
                )["result_snapshot"]
            return dict(encode({"document": doc, "lines": self.lines(repo, cid), "result": result}))

    def create(self, ctx: TenantContext, body: CountCreate, key: str) -> dict[str, Any]:
        ctx.require("counts.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "counts.write")
            one(
                repo,
                "SELECT id FROM med.warehouse WHERE id=:w AND entity_id=:e AND kind='PHYSICAL' "
                "AND archived_at IS NULL",
                {"w": body.warehouse_id, "e": body.entity_id},
            )
            if repo.rows(
                "SELECT id FROM med.stock_count WHERE warehouse_id=:w AND state IN "
                "('COUNTING','SUBMITTED','APPROVED')",
                {"w": body.warehouse_id},
            ):
                raise BusinessError("COUNT_EXISTS", "该仓库已有未完成盘点，请先处理或取消", 409)
            rows = repo.rows(
                BALANCES + " WHERE b.id=ANY(:ids) AND b.entity_id=:e AND b.warehouse_id=:w "
                "ORDER BY b.id",
                {"ids": sorted(body.balance_ids), "e": body.entity_id, "w": body.warehouse_id},
            )
            if len(rows) != len(body.balance_ids) or any(
                r["quality_status"] != "QUALIFIED"
                or r["serial_required"]
                or r["restricted_category"] is not None
                for r in rows
            ):
                raise BusinessError("COUNT_SCOPE", "请选择本仓库已有成本记录的普通合格批次", 422)
            pool_ids = sorted({r["cost_balance_id"] for r in rows})
            repo.rows(
                "SELECT id FROM med.stock_gate WHERE entity_id=:e AND sku_id=ANY(:ids) "
                "ORDER BY sku_id FOR UPDATE",
                {"e": body.entity_id, "ids": sorted({r["sku_id"] for r in rows})},
            )
            if repo.rows(
                """SELECT l.id FROM med.stock_count_line l JOIN med.stock_count d
                ON (d.tenant_id,d.id)=(l.tenant_id,l.count_id)
                WHERE l.cost_balance_id=ANY(:ids)
                  AND d.state IN ('COUNTING','SUBMITTED','APPROVED')""",
                {"ids": pool_ids},
            ):
                raise BusinessError("COUNT_EXISTS", "所选批次已在其他仓库盘点中", 409)
            related = repo.rows(
                """SELECT b.*,w.kind AS warehouse_kind FROM med.stock_balance b
                JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
                JOIN med.cost_balance c
                ON (c.tenant_id,c.entity_id,c.sku_id,c.lot_id)=
                   (b.tenant_id,b.entity_id,b.sku_id,b.lot_id)
                WHERE c.id=ANY(:ids) ORDER BY b.id FOR UPDATE OF b""",
                {"ids": pool_ids},
            )
            if any(r["warehouse_kind"] == "TRANSIT" and r["on_hand"] for r in related):
                raise BusinessError(
                    "COUNT_IN_TRANSIT", "所选批次仍有调拨在途数量，请先完成收货", 409
                )
            if any(r["reserved"] or r["blocked"] for r in related):
                raise BusinessError(
                    "COUNT_COMMITTED_STOCK",
                    "所选批次存在预占或冻结数量，请先处理关联业务再建盘点",
                    409,
                )
            repo.rows(
                "SELECT id FROM med.cost_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
                {"ids": pool_ids},
            )
            rows = repo.rows(
                BALANCES + " WHERE b.id=ANY(:ids) ORDER BY b.id", {"ids": sorted(body.balance_ids)}
            )
            cid = uuid4()
            number = document_number("SC", cid)
            repo.execute(
                """INSERT INTO med.stock_count
                (tenant_id,id,entity_id,warehouse_id,number,reason,created_by)
                VALUES(:t,:id,:e,:w,:n,:reason,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": cid,
                    "e": body.entity_id,
                    "w": body.warehouse_id,
                    "n": number,
                    "reason": body.reason,
                    "a": ctx.principal_id,
                },
            )
            for index, row in enumerate(rows, 1):
                repo.execute(
                    """INSERT INTO med.stock_count_line(tenant_id,id,count_id,line_no,
                    balance_id,cost_balance_id,stock_version,cost_version,book_qty,reserved_qty,
                    blocked_qty,pool_qty,pool_amount,product_snapshot,package_snapshot,created_by,
                    stock_entry_count,cost_entry_count)
                    VALUES(:t,:id,:c,:n,:b,:pool,:sv,:cv,:q,:r,:blocked,:pq,:pa,
                    CAST(:product AS jsonb),CAST(:package AS jsonb),:a,
                    (SELECT count(*) FROM med.stock_ledger WHERE balance_id=:b),
                    (SELECT count(*) FROM med.cost_ledger WHERE cost_balance_id=:pool))""",
                    {
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "c": cid,
                        "n": index,
                        "b": row["id"],
                        "pool": row["cost_balance_id"],
                        "sv": row["version"],
                        "cv": row["cost_version"],
                        "q": row["on_hand"],
                        "r": row["reserved"],
                        "blocked": row["blocked"],
                        "pq": row["pool_qty"],
                        "pa": row["pool_amount"],
                        "a": ctx.principal_id,
                        "product": json.dumps(
                            {
                                k: row[k]
                                for k in (
                                    "name",
                                    "specification",
                                    "manufacturer_name",
                                    "approval_number",
                                    "category",
                                    "sku_code",
                                )
                            }
                        ),
                        "package": json.dumps(
                            {
                                "base_unit": row["base_unit"],
                                "qty_scale": row["qty_scale"],
                                "numerator": 1,
                                "denominator": 1,
                            }
                        ),
                    },
                )
            audit(
                repo,
                ctx,
                "stock_count",
                cid,
                "inventory.count_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(cid), "number": number, "state": "COUNTING", "version": "1"}

        return self.finance.permanent(
            ctx,
            "counts.create",
            str(body.warehouse_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def unchanged(
        self, repo: SqlSession, lines: list[dict[str, Any]]
    ) -> dict[UUID, dict[str, Any]]:
        # Lock stock rows before their shared entity/lot cost pools, as other posting paths do.
        balances = {
            r["id"]: r
            for r in repo.rows(
                "SELECT * FROM med.stock_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
                {"ids": sorted({r["balance_id"] for r in lines})},
            )
        }
        pools = {
            r["id"]: r
            for r in repo.rows(
                "SELECT * FROM med.cost_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
                {"ids": sorted({r["cost_balance_id"] for r in lines})},
            )
        }
        for line in lines:
            b, p = balances[line["balance_id"]], pools[line["cost_balance_id"]]
            if b["quality_status"] != "QUALIFIED":
                raise BusinessError("COUNT_STALE", "库存质量状态已变化，请重新盘点", 409)
            if (
                b["version"],
                b["on_hand"],
                b["reserved"],
                b["blocked"],
                p["version"],
                p["qty"],
                p["amount"],
            ) != (
                line["stock_version"],
                line["book_qty"],
                line["reserved_qty"],
                line["blocked_qty"],
                line["cost_version"],
                line["pool_qty"],
                line["pool_amount"],
            ):
                raise BusinessError(
                    "COUNT_STALE", "盘点后库存、预占或成本已变化，请取消本单并重新实盘", 409
                )
            watermark = one(
                repo,
                """SELECT
                (SELECT count(*) FROM med.stock_ledger WHERE balance_id=:b) AS stock_entries,
                (SELECT count(*) FROM med.cost_ledger WHERE cost_balance_id=:c) AS cost_entries""",
                {"b": line["balance_id"], "c": line["cost_balance_id"]},
            )
            if (watermark["stock_entries"], watermark["cost_entries"]) != (
                line["stock_entry_count"],
                line["cost_entry_count"],
            ):
                raise BusinessError("COUNT_STALE", "盘点基准流水已变化，请重新盘点", 409)
        return pools

    def submit(self, ctx: TenantContext, cid: UUID, body: CountSubmit, key: str) -> dict[str, Any]:
        entity = self.count_scope(ctx, cid, "counts.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "counts.write")
            doc = one(repo, "SELECT * FROM med.stock_count WHERE id=:id FOR UPDATE", {"id": cid})
            version(doc, body.expected_version)
            if doc["state"] != "COUNTING":
                raise BusinessError("INVALID_STATE", "仅待实盘单可提交，请勿重复录入", 409)
            lines = self.lines(repo, cid)
            pools = self.unchanged(repo, lines)
            readings = {r.line_id: r for r in body.lines}
            if set(readings) != {r["id"] for r in lines}:
                raise BusinessError(
                    "COUNT_LINES", "必须逐行录入全部盘点范围，未盘数量不能默认为零", 422
                )
            lost: dict[UUID, Decimal] = {}
            for line in lines:
                reading = readings[line["id"]]
                actual = Decimal(reading.actual_qty)
                try:
                    if actual:
                        quantity(reading.actual_qty, sku_scale=line["qty_scale"])
                except ValueError as exc:
                    raise BusinessError("QTY_SCALE_INVALID", "实盘数量不符合商品精度", 422) from exc
                if reading.lot_no != line["lot_no"]:
                    raise BusinessError("COUNT_LOT_MISMATCH", "实盘批号与盘点范围不符", 409)
                if actual < line["reserved_qty"] + line["blocked_qty"]:
                    raise BusinessError(
                        "COUNT_COMMITTED_STOCK",
                        "实盘不足以覆盖预占或冻结数量，请先处理关联业务并重新盘点",
                        409,
                    )
                delta = actual - line["book_qty"]
                pool = pools[line["cost_balance_id"]]
                unit = (
                    Decimal(reading.gain_unit_cost) if reading.gain_unit_cost is not None else None
                )
                if unit is not None and not (delta > 0 and pool["qty"] == 0):
                    raise BusinessError(
                        "COUNT_COST_POLICY", "仅零成本数量批次盘盈时可填写核定单位成本", 422
                    )
                with localcontext() as precision:
                    precision.prec = 50
                    if delta < 0:
                        start = lost.get(pool["id"], Decimal(0))
                        end = start - delta
                        if end > pool["qty"]:
                            raise BusinessError("COUNT_COST_SHORT", "盘亏超过批次成本数量", 409)
                        cost = -portion(pool["amount"], start, end, pool["qty"], ".00000001")
                        lost[pool["id"]] = end
                    elif delta > 0:
                        if pool["qty"] == 0 and (pool["amount"] != 0 or unit is None):
                            raise BusinessError(
                                "COUNT_GAIN_COST_REQUIRED",
                                "零库存批次盘盈须核定单位成本并说明依据",
                                422,
                            )
                        cost = (
                            delta * (pool["amount"] / pool["qty"] if pool["qty"] else unit)
                        ).quantize(Decimal(".00000001"), rounding=ROUND_HALF_UP)
                    else:
                        cost = Decimal(0)
                if abs(cost) >= Decimal("1e16"):
                    raise BusinessError("BALANCE_OVERFLOW", "盘点成本金额超过上限", 422)
                repo.execute(
                    """INSERT INTO med.stock_count_measurement(tenant_id,line_id,count_id,
                    actual_qty,qty_delta,cost_delta,gain_unit_cost,note,created_by)
                    VALUES(:t,:line,:c,:q,:delta,:cost,:unit,:note,:a)""",
                    {
                        "t": ctx.tenant_id,
                        "line": line["id"],
                        "c": cid,
                        "q": actual,
                        "delta": delta,
                        "cost": cost,
                        "unit": unit,
                        "note": reading.note,
                        "a": ctx.principal_id,
                    },
                )
            repo.execute(
                """UPDATE med.stock_count SET state='SUBMITTED',submitted_by=:a,
                submitted_at=clock_timestamp(),submit_note=:note,version=version+1 WHERE id=:id""",
                {"id": cid, "a": ctx.principal_id, "note": body.reason},
            )
            audit(
                repo,
                ctx,
                "stock_count",
                cid,
                "inventory.count_submitted",
                body.model_dump(mode="json"),
            )
            return {"id": str(cid), "state": "SUBMITTED", "version": str(doc["version"] + 1)}

        return command(
            self.uow, ctx, "counts.submit", str(cid), key, body.model_dump(mode="json"), run
        )

    def review(self, ctx: TenantContext, cid: UUID, body: CountReview, key: str) -> dict[str, Any]:
        entity = self.count_scope(ctx, cid, "counts.review")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "counts.review")
            doc = one(repo, "SELECT * FROM med.stock_count WHERE id=:id FOR UPDATE", {"id": cid})
            version(doc, body.expected_version)
            if ctx.principal_id in (doc["created_by"], doc["submitted_by"]):
                raise BusinessError("MAKER_CHECKER", "盘点制单人及实盘录入人不能审核本单", 403)
            if doc["state"] != "SUBMITTED":
                raise BusinessError("INVALID_STATE", "仅已提交实盘单可审核", 409)
            if body.decision == "APPROVE":
                self.unchanged(repo, self.lines(repo, cid))
            state = "APPROVED" if body.decision == "APPROVE" else "CANCELLED"
            repo.execute(
                """UPDATE med.stock_count SET state=:state,reviewed_by=:a,
                reviewed_at=clock_timestamp(),review_note=:note,version=version+1 WHERE id=:id""",
                {"id": cid, "state": state, "a": ctx.principal_id, "note": body.reason},
            )
            audit(
                repo,
                ctx,
                "stock_count",
                cid,
                "inventory.count_reviewed",
                body.model_dump(mode="json"),
            )
            return {"id": str(cid), "state": state, "version": str(doc["version"] + 1)}

        return command(
            self.uow, ctx, "counts.review", str(cid), key, body.model_dump(mode="json"), run
        )

    def cancel(self, ctx: TenantContext, cid: UUID, body: Transition, key: str) -> dict[str, Any]:
        entity = self.count_scope(ctx, cid, "counts.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "counts.write")
            doc = one(repo, "SELECT * FROM med.stock_count WHERE id=:id FOR UPDATE", {"id": cid})
            version(doc, body.expected_version)
            if doc["state"] not in {"COUNTING", "SUBMITTED", "APPROVED"}:
                raise BusinessError("INVALID_STATE", "已过账或已取消盘点不能取消", 409)
            repo.execute(
                """UPDATE med.stock_count SET state='CANCELLED',cancel_note=:note,
                version=version+1 WHERE id=:id""",
                {"id": cid, "note": body.reason},
            )
            audit(
                repo,
                ctx,
                "stock_count",
                cid,
                "inventory.count_cancelled",
                body.model_dump(mode="json"),
            )
            return {"id": str(cid), "state": "CANCELLED", "version": str(doc["version"] + 1)}

        return command(
            self.uow, ctx, "counts.cancel", str(cid), key, body.model_dump(mode="json"), run
        )
