import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.application.counts import CountService
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: CountService, ctx: TenantContext, cid: UUID, body: Transition, key: str
) -> dict[str, Any]:
    entity = service.count_scope(ctx, cid, "counts.post")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "counts.post")
        doc = one(repo, "SELECT * FROM med.stock_count WHERE id=:id FOR UPDATE", {"id": cid})
        if doc["state"] == "POSTED":
            return dict(
                one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["posting_id"]},
                )["result_snapshot"]
            )
        version(doc, body.expected_version)
        if doc["state"] != "APPROVED":
            raise BusinessError("COUNT_NOT_APPROVED", "盘点必须先完成独立审核", 409)
        if ctx.principal_id in (doc["created_by"], doc["submitted_by"]):
            raise BusinessError("MAKER_CHECKER", "制单或实盘录入人不能过账本单", 403)
        day = service.finance.today(repo)
        now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
        service.finance.period(repo, ctx, entity, day)
        lines = service.lines(repo, cid)
        goods = {
            r["id"]: r
            for r in repo.rows(
                """SELECT s.id,s.serial_required,p.restricted_category,
            p.approval_state,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current,g.blocked FROM med.sku s
            JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
            JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id) AND g.entity_id=:e
            WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p,g""",
                {"e": entity, "ids": sorted({r["sku_id"] for r in lines})},
            )
        }
        lots = {
            r["id"]: r
            for r in repo.rows(
                "SELECT * FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
                {"ids": sorted({r["lot_id"] for r in lines})},
            )
        }
        pools = service.unchanged(repo, lines)
        for line in lines:
            sku, lot = goods[line["sku_id"]], lots[line["lot_id"]]
            if sku["serial_required"] or sku["restricted_category"] is not None:
                raise BusinessError("COUNT_PRODUCT_UNSUPPORTED", "当前仅支持普通批次盘点", 409)
            if line["qty_delta"] > 0 and (
                sku["blocked"]
                or sku["approval_state"] != "APPROVED"
                or not sku["qualification_current"]
                or lot["quality_blocked"]
                or not lot["expiry_exclusive"]
                or lot["expiry_exclusive"] <= now
            ):
                raise BusinessError(
                    "COUNT_GAIN_INELIGIBLE", "冻结、过期或资质无效商品不能盘盈为可售库存", 409
                )
            pool = pools[line["cost_balance_id"]]
            pool["qty"] += line["qty_delta"]
            pool["amount"] += line["cost_delta"]
        for pool in pools.values():
            if pool["qty"] < 0 or pool["amount"] < 0 or (pool["qty"] == 0 and pool["amount"] != 0):
                raise BusinessError("COUNT_COST_INVALID", "盘点后成本数量与金额不一致", 409)
            if pool["qty"] >= Decimal("1e14") or pool["amount"] >= Decimal("1e16"):
                raise BusinessError("BALANCE_OVERFLOW", "盘点后成本池超过上限", 422)
        changed = [r for r in lines if r["qty_delta"] != 0]
        pid = uuid4()
        result = {
            "id": str(cid),
            "posting_id": str(pid),
            "state": "POSTED",
            "version": str(doc["version"] + 1),
            "changed_lines": len(changed),
            "gain_cost": format(
                sum((r["cost_delta"] for r in changed if r["qty_delta"] > 0), Decimal(0)), ".8f"
            ),
            "loss_cost": format(
                -sum((r["cost_delta"] for r in changed if r["qty_delta"] < 0), Decimal(0)), ".8f"
            ),
            "cost_delta": format(sum((r["cost_delta"] for r in changed), Decimal(0)), ".8f"),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
            source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
            VALUES(:t,:p,:a,:e,'STOCK_COUNT',:id,'COUNT_POSTED',:id,:day,:now,:req,
            CAST(:result AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "p": pid,
                "a": ctx.principal_id,
                "e": entity,
                "id": cid,
                "day": day,
                "now": now,
                "req": ctx.request_id,
                "result": json.dumps(result),
            },
        )
        # Closing the document releases its database freeze within this same transaction.
        repo.execute(
            """UPDATE med.stock_count SET state='POSTED',posting_id=:p,version=version+1
            WHERE id=:id""",
            {"id": cid, "p": pid},
        )
        # Apply final pool totals once, including multiple locations sharing the same pool.
        for pool_id in sorted({r["cost_balance_id"] for r in changed}):
            pool = pools[pool_id]
            repo.execute(
                """UPDATE med.cost_balance SET qty=:qty,amount=:amount,version=version+1,
                updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
                {
                    "id": pool_id,
                    "qty": pool["qty"],
                    "amount": pool["amount"],
                    "a": ctx.principal_id,
                },
            )
        for index, line in enumerate(changed, 1):
            pl = uuid4()
            params = {
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "pl": pl,
                "p": pid,
                "source": line["id"],
                "n": index,
                "s": line["sku_id"],
                "q": abs(line["qty_delta"]),
                "qty": line["qty_delta"],
                "cost": line["cost_delta"],
                "b": line["balance_id"],
                "pool": line["cost_balance_id"],
                "now": now,
                "day": day,
                "action": "COUNT_GAIN" if line["qty_delta"] > 0 else "COUNT_LOSS",
                "product": json.dumps(line["product_snapshot"]),
                "package": json.dumps(line["package_snapshot"]),
            }
            repo.execute(
                """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,source_line_id,
                line_no,sku_id,base_qty,product_snapshot,package_snapshot)
                VALUES(:t,:pl,:a,:p,:source,:n,:s,:q,CAST(:product AS jsonb),
                CAST(:package AS jsonb))""",
                params,
            )
            repo.execute(
                """UPDATE med.stock_balance SET on_hand=on_hand+:qty,version=version+1,
                updated_by=:a,updated_at=clock_timestamp() WHERE id=:b""",
                params,
            )
            repo.execute(
                """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,balance_id,qty_delta,event_type,posted_at)
                VALUES(:t,:id,:a,:pl,0,:b,:qty,:action,:now)""",
                {**params, "id": uuid4()},
            )
            repo.execute(
                """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
                effect_index,cost_balance_id,qty_delta,amount_delta,business_date)
                VALUES(:t,:id,:a,:pl,0,:pool,:qty,:cost,:day)""",
                {**params, "id": uuid4()},
            )
        audit(
            repo,
            ctx,
            "stock_count",
            cid,
            "inventory.count_posted",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "stock_count",
            cid,
            doc["version"] + 1,
            "inventory.count_posted",
            result,
        )
        return result

    return command(
        service.uow, ctx, "counts.post", str(cid), key, body.model_dump(mode="json"), run
    )
