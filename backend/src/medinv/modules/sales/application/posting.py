import json
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.catalog.application.profile import product_snapshot
from medinv.modules.inventory.domain.decimal_values import money_for_posting
from medinv.modules.procurement.application.service import ZONE, one, version
from medinv.modules.sales.api.schemas import ShipmentPost
from medinv.modules.sales.application.fulfillment import active_manifest, shipment_order
from medinv.modules.sales.application.service import SalesService
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: SalesService, ctx: TenantContext, sid: UUID, body: ShipmentPost, key: str
) -> dict[str, Any]:
    oid = shipment_order(service, ctx, sid, "sales.post")
    body_hash = fingerprint(body.model_dump(mode="json"))

    def execute(repo: SqlSession) -> dict[str, Any]:
        order, order_lines, _ = service.locked_order(repo, ctx, oid, "sales.post", False)
        sh = one(repo, "SELECT * FROM med.shipment WHERE id=:id FOR UPDATE", {"id": sid})
        if sh["state"] == "POSTED":
            saved = one(
                repo,
                "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                {"id": sh["posting_id"]},
            )["result_snapshot"]
            if saved["request_hash"] != body_hash:
                raise BusinessError("POSTING_CONFLICT", "子单已按另一请求出库", 409)
            return dict(saved["response"])
        version(sh, body.expected_version)
        if (
            sh["state"] != "VERIFIED"
            or not sh["verified_by"]
            or sh["picked_by"] == sh["verified_by"]
        ):
            raise BusinessError("NOT_VERIFIED", "必须由独立成员完成出库复核", 409)
        customer = service.customer(repo, order["entity_id"], order["customer_id"])
        skus = service.products(
            repo, order["entity_id"], [entry["sku_id"] for entry in order_lines], customer
        )
        rows = active_manifest(service, repo, order, sh)
        now = service.now(repo)
        day = now.astimezone(ZONE).date()
        period = day.strftime("%Y-%m")
        repo.execute(
            """INSERT INTO med.accounting_period(tenant_id,entity_id,period)
            VALUES(:t,:e,:p) ON CONFLICT DO NOTHING""",
            {"t": ctx.tenant_id, "e": order["entity_id"], "p": period},
        )
        accounting = one(
            repo,
            """SELECT state FROM med.accounting_period
            WHERE entity_id=:e AND period=:p FOR SHARE""",
            {"e": order["entity_id"], "p": period},
        )
        if accounting["state"] != "OPEN":
            raise BusinessError("PERIOD_CLOSED", "当前会计期间已关闭", 409)
        demand = one(
            repo, "SELECT * FROM med.inventory_demand WHERE id=:id FOR UPDATE", {"id": oid}
        )
        if demand["state"] != "APPROVED" or not demand["checks_completed"]:
            raise BusinessError("SOURCE_NOT_APPROVED", "销售来源需求已失效", 409)
        demand_lines = {
            r["id"]: r
            for r in repo.rows(
                """SELECT * FROM med.inventory_demand_line
            WHERE demand_id=:id ORDER BY id FOR UPDATE""",
                {"id": oid},
            )
        }
        originals = {entry["id"]: entry for entry in order_lines}
        pid, eid = uuid4(), uuid4()
        ledger_ids = [uuid4() for _ in rows]
        result = {
            "posted_document_id": str(sid),
            "posting_id": str(pid),
            "new_version": str(sh["version"] + 1),
            "ledger_ids": list(map(str, ledger_ids)),
            "event_id": str(eid),
            "posted_at": now.isoformat(),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
            source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
            VALUES(:t,:id,:a,:e,'SHIPMENT',:s,'SALES_SHIPPED',:s,:day,:now,:req,CAST(:result
            AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "id": pid,
                "a": ctx.principal_id,
                "e": order["entity_id"],
                "s": sid,
                "day": day,
                "now": now,
                "req": ctx.request_id,
                "result": json.dumps({"request_hash": body_hash, "response": result}),
            },
        )
        gross_total = Decimal(0)
        net_total = Decimal(0)
        for index, (row, ledger) in enumerate(zip(rows, ledger_ids, strict=True), 1):
            qty = row["allocated_qty"]
            if row["picked_qty"] != qty or row["scanned_lot_no"] != row["lot_no"]:
                raise BusinessError("PICK_MISMATCH", "拣货记录与当前批次数量不一致", 409)
            source = demand_lines[row["source_line_id"]]
            original = originals[row["source_line_id"]]
            if (
                source["reserved_qty"] < qty
                or source["fulfilled_qty"] + qty > source["requested_qty"]
            ):
                raise BusinessError("DEMAND_EXCEEDED", "发货超出订单已预占或未履约数量", 409)
            pools = repo.rows(
                """SELECT * FROM med.cost_balance WHERE entity_id=:e AND owner_id=:e
                AND sku_id=:s AND lot_id=:l FOR UPDATE""",
                {"e": order["entity_id"], "s": row["sku_id"], "l": row["lot_id"]},
            )
            if not pools or pools[0]["qty"] < qty:
                raise BusinessError(
                    "COST_BASIS_MISSING", "批次成本数量不足，不能使用无成本快照出库", 409
                )
            pool = pools[0]
            # Weighted batch cost at 8 decimals; the final issue consumes every remaining cent.
            cost = (
                pool["amount"]
                if qty == pool["qty"]
                else (pool["amount"] * qty / pool["qty"]).quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )
            )
            cost = min(cost, pool["amount"])
            # Cumulative allocation of full order-line money prevents split-shipment rounding drift.
            old_qty, new_qty = source["fulfilled_qty"], source["fulfilled_qty"] + qty
            gross = money_for_posting(original["gross_amount"] * new_qty / original["qty"]) - (
                money_for_posting(original["gross_amount"] * old_qty / original["qty"])
            )
            # Distribute net against cumulative gross to keep each segment's tax non-negative.
            if original["gross_amount"]:
                old_g = money_for_posting(original["gross_amount"] * old_qty / original["qty"])
                new_g = old_g + gross
                net = money_for_posting(
                    original["net_amount"] * new_g / original["gross_amount"]
                ) - (money_for_posting(original["net_amount"] * old_g / original["gross_amount"]))
            else:
                net = Decimal(0)
            gross_total += gross
            net_total += net
            pl = uuid4()
            sku = skus[row["sku_id"]]
            repo.execute(
                """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,
                source_line_id,line_no,sku_id,base_qty,product_snapshot,package_snapshot,
                gross_amount,net_amount)
                VALUES(:t,:id,:a,:p,:source,:n,:s,:q,CAST(:product AS
                jsonb),CAST(:package AS jsonb),:gross,:net)""",
                {
                    "t": ctx.tenant_id,
                    "id": pl,
                    "gross": gross,
                    "net": net,
                    "a": ctx.principal_id,
                    "p": pid,
                    "source": row["id"],
                    "n": index,
                    "s": row["sku_id"],
                    "q": qty,
                    "product": json.dumps(product_snapshot(sku)),
                    "package": json.dumps(
                        original["package_snapshot"]
                        or {
                            "base_unit": sku["base_unit"],
                            "qty_scale": sku["qty_scale"],
                            "numerator": 1,
                            "denominator": 1,
                        }
                    ),
                },
            )
            p = {
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "q": qty,
                "cost": cost,
                "b": row["balance_id"],
                "pool": pool["id"],
                "line": row["source_line_id"],
                "allocation": row["allocation_id"],
                "pl": pl,
                "now": now,
                "day": day,
            }
            repo.execute(
                """UPDATE med.stock_balance SET on_hand=on_hand-:q,reserved=reserved-:q,
                version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:b""",
                p,
            )
            repo.execute(
                """UPDATE med.cost_balance SET qty=qty-:q,amount=amount-:cost,
                version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:pool""",
                p,
            )
            repo.execute(
                """UPDATE med.inventory_demand_line SET reserved_qty=reserved_qty-:q,
                fulfilled_qty=fulfilled_qty+:q WHERE id=:line""",
                p,
            )
            source["fulfilled_qty"] = new_qty
            source["reserved_qty"] -= qty
            repo.execute(
                """UPDATE med.reservation_allocation SET consumed_qty=consumed_qty+:q,
                version=version+1,updated_by=:a,updated_at=clock_timestamp()
                WHERE id=:allocation""",
                p,
            )
            repo.execute(
                """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,balance_id,qty_delta,event_type,posted_at)
                VALUES(:t,:id,:a,:pl,0,:b,-:q,'SALES_SHIPPED',:now)""",
                {**p, "id": ledger},
            )
            repo.execute(
                """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,cost_balance_id,qty_delta,amount_delta,business_date)
                VALUES(:t,:id,:a,:pl,0,:pool,-:q,-:cost,:day)""",
                {**p, "id": uuid4()},
            )
        if gross_total > order["remaining_exposure"]:
            raise BusinessError("CREDIT_INCONSISTENT", "订单信用敞口与发货金额不一致", 409)
        repo.execute(
            """INSERT INTO med.receivable_obligation(tenant_id,id,entity_id,customer_id,
            shipment_id,posting_id,gross_amount,net_amount,tax_amount,due_date,policy_version)
            VALUES(:t,:id,:e,:c,:s,:p,:gross,:net,:tax,:due,'SALES_CUMULATIVE_V1')""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "e": order["entity_id"],
                "c": order["customer_id"],
                "s": sid,
                "p": pid,
                "gross": gross_total,
                "net": net_total,
                "tax": gross_total - net_total,
                "due": day + timedelta(days=order["credit_days"]),
            },
        )
        repo.execute(
            """UPDATE med.credit_account SET exposure=exposure-:gross,
            receivable=receivable+:gross,version=version+1 WHERE entity_id=:e AND customer_id=:c""",
            {"gross": gross_total, "e": order["entity_id"], "c": order["customer_id"]},
        )
        full = all(
            entry["fulfilled_qty"] == entry["requested_qty"] for entry in demand_lines.values()
        )
        repo.execute(
            """UPDATE med.inventory_demand SET state=:s,version=version+1 WHERE id=:id""",
            {"s": "CLOSED" if full else "APPROVED", "id": oid},
        )
        repo.execute(
            """UPDATE med.reservation SET state='CONSUMED',version=version+1,
            updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"a": ctx.principal_id, "id": sh["reservation_id"]},
        )
        repo.execute(
            """UPDATE med.sales_order SET state=:s,remaining_exposure=remaining_exposure-:g,
            version=version+1 WHERE id=:id""",
            {"s": "FULFILLED" if full else "APPROVED", "g": gross_total, "id": oid},
        )
        repo.execute(
            "UPDATE med.shipment SET state='POSTED',posting_id=:p,version=version+1 WHERE id=:id",
            {"p": pid, "id": sid},
        )
        audit(repo, ctx, "shipment", sid, "inventory.shipment_posted", result)
        event(
            repo, ctx, eid, "shipment", sid, sh["version"] + 1, "inventory.shipment_posted", result
        )
        return result

    return command(
        service.uow, ctx, "sales.post", str(sid), key, body.model_dump(mode="json"), execute
    )
