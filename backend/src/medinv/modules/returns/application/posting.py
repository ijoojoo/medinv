import json
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import one, version
from medinv.modules.returns.application import custody
from medinv.modules.returns.application.service import ReturnService
from medinv.modules.settlement.application.allocation import credit_delta
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: ReturnService, ctx: TenantContext, rid: UUID, body: Transition, key: str
) -> dict[str, Any]:
    entity = service.return_scope(ctx, rid, "returns.post")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, "returns.post")
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid})
        if doc["state"] == "POSTED":
            return dict(
                one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["posting_id"]},
                )["result_snapshot"]
            )
        version(doc, body.expected_version)
        if doc["state"] != "APPROVED" or doc["created_by"] == ctx.principal_id:
            raise BusinessError("RETURN_NOT_REVIEWED", "退货必须独立复核，制单人不能过账", 403)
        now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
        day = service.today(repo)
        service.period(repo, ctx, entity, day)
        if doc["kind"] != "RETAIL":
            item = one(
                repo,
                "SELECT * FROM med.settlement_item WHERE posting_id=:p",
                {"p": doc["source_posting_id"]},
            )
            if item["outstanding_amount"] < doc["gross_amount"]:
                raise BusinessError(
                    "RETURN_REVERSE_ALLOCATIONS",
                    "原单未结余额不足以抵减本次退货，请先撤销相关核销",
                    409,
                )
        lines = service.selected_lines(repo, doc)
        incoming = doc["kind"] in {"SALES", "RETAIL"}
        # The entity lock serializes all supported posting/settlement commands; row locks
        # also coordinate stock reservations and quality control changes.
        skus = repo.rows(
            """SELECT s.id,p.approval_state,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current,
            p.restricted_category,s.serial_required,g.blocked
          FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
          JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id) AND g.entity_id=:e
          WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p,g""",
            {"e": entity, "ids": sorted({r["sku_id"] for r in lines})},
        )
        if len(skus) != len({r["sku_id"] for r in lines}) or any(
            r["serial_required"]
            or r["restricted_category"] is not None
            or (
                incoming
                and (
                    r["blocked"]
                    or r["approval_state"] != "APPROVED"
                    or not r["qualification_current"]
                )
            )
            for r in skus
        ):
            raise BusinessError("RETURN_PRODUCT_INELIGIBLE", "商品不满足当前普通批次退货条件", 409)
        lots = repo.rows(
            "SELECT * FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
            {"ids": sorted({r["lot_id"] for r in lines})},
        )
        if incoming and any(
            r["quality_blocked"] or not r["expiry_exclusive"] or r["expiry_exclusive"] <= now
            for r in lots
        ):
            raise BusinessError("RETURN_LOT_INELIGIBLE", "冻结或过期退回品不能恢复可售库存", 409)
        pid, eid = uuid4(), uuid4()
        action = {
            "SALES": "SALES_RETURNED",
            "RETAIL": "RETAIL_RETURNED",
            "PURCHASE": "PURCHASE_RETURNED",
        }[doc["kind"]]
        # Store the result only after every effect has been validated and prepared.
        prepared = []
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
        total_cost = Decimal(0)
        for line in lines:
            qty = line["base_qty"]
            balance, pool = balances[line["balance_id"]], pools[line["cost_balance_id"]]
            if (
                balance["quality_status"] != "QUALIFIED"
                or qty <= 0
                or qty > line["source_qty"]
                or (line["qty_delta"] != (-line["source_qty"] if incoming else line["source_qty"]))
            ):
                raise BusinessError("RETURN_SOURCE_UNSUPPORTED", "原库存效果不符合退货条件", 409)
            if not incoming and (
                balance["on_hand"] - balance["reserved"] - balance["blocked"] < qty
                or pool["qty"] < qty
            ):
                raise BusinessError("RETURN_STOCK_SHORT", "原库位可用库存或批次成本数量不足", 409)
            with localcontext() as precision:
                precision.prec = 50
                cost = (
                    line["original_cost"]
                    if incoming
                    else (
                        pool["amount"]
                        if pool["qty"] == qty
                        else min(
                            pool["amount"],
                            (pool["amount"] * qty / pool["qty"]).quantize(
                                Decimal("0.00000001"), rounding=ROUND_HALF_UP
                            ),
                        )
                    )
                )
            sign = 1 if incoming else -1
            balance["on_hand"] += sign * qty
            pool["qty"] += sign * qty
            pool["amount"] += sign * cost
            if (
                balance["on_hand"] >= Decimal("1e14")
                or pool["qty"] >= Decimal("1e14")
                or pool["amount"] >= Decimal("1e16")
            ):
                raise BusinessError("BALANCE_OVERFLOW", "退货导致库存或成本池超过上限", 422)
            prepared.append((line, sign * qty, sign * cost))
            total_cost += cost
        result = {
            "id": str(rid),
            "posting_id": str(pid),
            "state": "POSTED",
            "version": str(doc["version"] + 1),
            "gross_amount": format(doc["gross_amount"], ".2f"),
            "cost_amount": format(total_cost, ".8f"),
            "purchase_cost_variance": format(
                doc["net_amount"] - total_cost if not incoming else Decimal(0), ".8f"
            ),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
         
              source_id,action,segment_id,business_date,posted_at,request_id,reversal_of,result_snapshot)
          VALUES(:t,:id,:a,:e,'RETURN',:r,:action,:r,:day,:now,:req,:origin,CAST(:result AS
              jsonb))""",
            {
                "t": ctx.tenant_id,
                "id": pid,
                "a": ctx.principal_id,
                "e": entity,
                "r": rid,
                "action": action,
                "day": day,
                "now": now,
                "req": ctx.request_id,
                "origin": doc["source_posting_id"],
                "result": json.dumps(result),
            },
        )
        for index, (line, qty, cost) in enumerate(prepared, 1):
            pl = uuid4()
            params = {
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "id": pl,
                "p": pid,
                "s": line["sku_id"],
                "q": abs(qty),
                "source": line["id"],
                "n": index,
                "qty": qty,
                "cost": cost,
                "balance": line["balance_id"],
                "pool": line["cost_balance_id"],
                "now": now,
                "day": day,
                "action": action,
                "product": json.dumps(line["product_snapshot"]),
                "package": json.dumps(line["package_snapshot"]),
                "gross": line["gross_amount"],
                "net": line["net_amount"],
            }
            repo.execute(
                """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,
              source_line_id,line_no,sku_id,base_qty,product_snapshot,package_snapshot,
              gross_amount,net_amount)
              VALUES(:t,:id,:a,:p,:source,:n,:s,:q,CAST(:product AS jsonb),CAST(:package AS
              jsonb),:gross,:net)""",
                params,
            )
            repo.execute(
                """UPDATE med.stock_balance SET on_hand=on_hand+:qty,
              version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:balance""",
                params,
            )
            repo.execute(
                """UPDATE med.cost_balance SET qty=qty+:qty,amount=amount+:cost,
              version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:pool""",
                params,
            )
            repo.execute(
                """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
              effect_index,balance_id,qty_delta,event_type,posted_at,reversal_of)
              VALUES(:t,:ledger,:a,:id,0,:balance,:qty,:action,:now,:origin)""",
                {**params, "ledger": uuid4(), "origin": line["stock_entry_id"]},
            )
            repo.execute(
                """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
             
              effect_index,cost_balance_id,qty_delta,amount_delta,business_date,origin_cost_entry_id)
              VALUES(:t,:ledger,:a,:id,0,:pool,:qty,:cost,:day,:origin)""",
                {**params, "ledger": uuid4(), "origin": line["cost_entry_id"]},
            )
        if incoming:
            if doc["workflow_version"] == 2:
                custody.close(repo, ctx, rid, "ACCEPTED", body.reason)
            if doc["kind"] == "SALES":
                credit_delta(
                    repo,
                    ctx,
                    {"direction": "RECEIPT", "entity_id": entity, "party_id": doc["party_id"]},
                    -doc["gross_amount"],
                )
        repo.execute(
            """UPDATE med.return_document SET state='POSTED',posting_id=:p,
          version=version+1 WHERE id=:id""",
            {"p": pid, "id": rid},
        )
        audit(
            repo, ctx, "return", rid, "inventory.return_posted", {**result, "reason": body.reason}
        )
        event(repo, ctx, eid, "return", rid, doc["version"] + 1, "inventory.return_posted", result)
        return result

    return command(
        service.uow, ctx, "returns.post", str(rid), key, body.model_dump(mode="json"), run
    )
