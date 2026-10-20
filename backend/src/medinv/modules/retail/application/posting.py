import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.domain.decimal_values import portion
from medinv.modules.procurement.application.service import one, version
from medinv.modules.retail.api.schemas import RetailComplete
from medinv.modules.retail.application.service import RetailService
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def complete(
    service: RetailService, ctx: TenantContext, sid: UUID, body: RetailComplete, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, sid, "retail.sell")
    body_hash = fingerprint(body.model_dump(mode="json"))

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "retail.sell")
        sale = one(repo, "SELECT * FROM med.retail_sale WHERE id=:id FOR UPDATE", {"id": sid})
        if sale["state"] == "COMPLETED":
            saved = one(
                repo,
                "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                {"id": sale["posting_id"]},
            )["result_snapshot"]
            if saved["request_hash"] != body_hash:
                raise BusinessError("POSTING_CONFLICT", "该零售单已按另一请求完成", 409)
            return dict(saved["response"])
        version(sale, body.expected_version)
        if sale["state"] != "PAID_PENDING_POST":
            raise BusinessError("RETAIL_NOT_PAID", "请先登记实际现金收款", 409)
        original = {r["id"]: r for r in service.lines(repo, sid)}
        service.products(repo, entity, [r["sku_id"] for r in original.values()])
        reservation = one(
            repo,
            "SELECT * FROM med.reservation WHERE id=:id FOR UPDATE",
            {"id": sale["reservation_id"]},
        )
        demand = one(
            repo, "SELECT * FROM med.inventory_demand WHERE id=:id FOR UPDATE", {"id": sid}
        )
        if (
            reservation["state"] != "ACTIVE"
            or reservation["protected_until"] is None
            or demand["state"] != "PAID_PROTECTED"
        ):
            raise BusinessError("RESERVATION_INVALID", "付款保护或预占状态不一致，请核查", 409)
        demand_lines = {
            r["id"]: r
            for r in repo.rows(
                """SELECT * FROM med.inventory_demand_line
            WHERE demand_id=:id ORDER BY id FOR UPDATE""",
                {"id": sid},
            )
        }
        rows = service.allocations(repo, sale["reservation_id"])
        if not rows:
            raise BusinessError("EMPTY_RETAIL", "没有可出库的预占明细", 409)
        lots = repo.rows(
            "SELECT * FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
            {"ids": sorted({r["lot_id"] for r in rows})},
        )
        now = service.sales.now(repo)
        if any(
            r["quality_blocked"] or not r["expiry_exclusive"] or r["expiry_exclusive"] <= now
            for r in lots
        ):
            raise BusinessError(
                "RETAIL_LOT_INELIGIBLE", "批次冻结或过期，保留收款并等待重试或退款", 409
            )
        balances = {
            r["id"]: r
            for r in repo.rows(
                "SELECT * FROM med.stock_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
                {"ids": sorted({r["balance_id"] for r in rows})},
            )
        }
        expected = {str(r["id"]): (r["allocated_qty"], r["lot_no"]) for r in rows}
        actual = {str(r.allocation_id): (Decimal(r.qty), r.lot_no) for r in body.lines}
        if expected != actual:
            raise BusinessError(
                "RETAIL_CHECK_MISMATCH", "交付数量、批号必须与预占清单逐行一致", 409
            )
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        prepared = []
        total_cost = Decimal(0)
        cumulative = {key: Decimal(0) for key in original}
        for row in rows:
            qty = row["allocated_qty"]
            line = original[row["source_line_id"]]
            balance = balances[row["balance_id"]]
            demand_line = demand_lines[line["id"]]
            if (
                row["released_qty"]
                or row["consumed_qty"]
                or balance["quality_status"] != "QUALIFIED"
                or balance["reserved"] < qty
                or balance["on_hand"] - balance["blocked"] < qty
                or demand_line["reserved_qty"] < qty
            ):
                raise BusinessError("RESERVATION_INVALID", "当前预占库存不满足交付条件", 409)
            pool = one(
                repo,
                """SELECT * FROM med.cost_balance WHERE entity_id=:e AND owner_id=:e
                AND sku_id=:s AND lot_id=:l FOR UPDATE""",
                {"e": entity, "s": row["sku_id"], "l": row["lot_id"]},
            )
            if pool["qty"] < qty:
                raise BusinessError("COST_BASIS_MISSING", "批次成本数量不足，收款保留待处理", 409)
            cost = (
                pool["amount"]
                if pool["qty"] == qty
                else min(
                    pool["amount"],
                    (pool["amount"] * qty / pool["qty"]).quantize(
                        Decimal(".00000001"), rounding=ROUND_HALF_UP
                    ),
                )
            )
            start = cumulative[line["id"]]
            gross = portion(line["gross_amount"], start, start + qty, line["qty"])
            old_g = portion(line["gross_amount"], Decimal(0), start, line["qty"])
            net = (
                portion(line["net_amount"], old_g, old_g + gross, line["gross_amount"])
                if line["gross_amount"]
                else Decimal(0)
            )
            cumulative[line["id"]] += qty
            balance["on_hand"] -= qty
            balance["reserved"] -= qty
            demand_line["reserved_qty"] -= qty
            # Each following split of the same pool reads these uncommitted changes.
            repo.execute(
                """UPDATE med.cost_balance SET qty=qty-:qty,amount=amount-:cost,
                version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
                {"id": pool["id"], "qty": qty, "cost": cost, "a": ctx.principal_id},
            )
            prepared.append(
                {**row, "qty": qty, "cost": cost, "pool": pool["id"], "gross": gross, "net": net}
            )
            total_cost += cost
        if (
            any(cumulative[k] != r["qty"] for k, r in original.items())
            or sum(r["gross"] for r in prepared) != sale["gross_amount"]
            or sum(r["net"] for r in prepared) != sale["net_amount"]
        ):
            raise BusinessError("RETAIL_MANIFEST_INVALID", "预占数量或金额与原零售单不一致", 409)
        pid = uuid4()
        result = {
            "id": str(sid),
            "state": "COMPLETED",
            "version": str(sale["version"] + 1),
            "posting_id": str(pid),
            "gross_amount": format(sale["gross_amount"], ".2f"),
            "cost_amount": format(total_cost, ".8f"),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
            source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
            VALUES(:t,:id,:a,:e,'RETAIL_SALE',:s,'RETAIL_SOLD',:s,:day,:now,:req,
              CAST(:result AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "id": pid,
                "a": ctx.principal_id,
                "e": entity,
                "s": sid,
                "day": day,
                "now": now,
                "req": ctx.request_id,
                "result": json.dumps({"request_hash": body_hash, "response": result}),
            },
        )
        for index, row in enumerate(prepared, 1):
            line = original[row["source_line_id"]]
            params = {
                **row,
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "pl": uuid4(),
                "p": pid,
                "n": index,
                "source": line["id"],
                "product": json.dumps(line["product_snapshot"]),
                "package": json.dumps(line["package_snapshot"]),
                "now": now,
                "day": day,
            }
            repo.execute(
                """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,
                source_line_id,line_no,sku_id,base_qty,product_snapshot,package_snapshot,gross_amount,net_amount)
                VALUES(:t,:pl,:a,:p,:source,:n,:sku_id,:qty,CAST(:product AS jsonb),
                CAST(:package AS jsonb),:gross,:net)""",
                params,
            )
            repo.execute(
                """UPDATE med.stock_balance SET on_hand=on_hand-:qty,reserved=reserved-:qty,
                version=version+1,updated_by=:a,updated_at=clock_timestamp()
                WHERE id=:balance_id""",
                params,
            )
            repo.execute(
                """UPDATE med.inventory_demand_line SET reserved_qty=reserved_qty-:qty,
                fulfilled_qty=fulfilled_qty+:qty WHERE id=:source""",
                params,
            )
            repo.execute(
                """UPDATE med.reservation_allocation SET consumed_qty=consumed_qty+:qty,
                version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
                params,
            )
            repo.execute(
                """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,balance_id,qty_delta,event_type,posted_at)
                VALUES(:t,:ledger,:a,:pl,0,:balance_id,-:qty,'RETAIL_SOLD',:now)""",
                {**params, "ledger": uuid4()},
            )
            repo.execute(
                """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,cost_balance_id,qty_delta,amount_delta,business_date)
                VALUES(:t,:ledger,:a,:pl,0,:pool,-:qty,-:cost,:day)""",
                {**params, "ledger": uuid4()},
            )
        repo.execute(
            "UPDATE med.inventory_demand SET state='CLOSED',version=version+1 WHERE id=:id",
            {"id": sid},
        )
        repo.execute(
            """UPDATE med.reservation SET state='CONSUMED',protected_until=NULL,
            version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"id": sale["reservation_id"], "a": ctx.principal_id},
        )
        repo.execute(
            "UPDATE med.retail_sale SET state='COMPLETED',posting_id=:p,"
            "version=version+1 WHERE id=:id",
            {"id": sid, "p": pid},
        )
        audit(
            repo,
            ctx,
            "retail_sale",
            sid,
            "inventory.retail_completed",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "retail_sale",
            sid,
            sale["version"] + 1,
            "inventory.retail_completed",
            result,
        )
        return result

    return command(
        service.uow, ctx, "retail.complete", str(sid), key, body.model_dump(mode="json"), run
    )
