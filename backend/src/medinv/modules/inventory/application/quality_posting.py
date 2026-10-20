import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.application.quality import QualityService
from medinv.modules.inventory.application.stock_movements import balance, lock_goods, move
from medinv.modules.inventory.application.stock_writeoffs import loss_cost, write_off
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: QualityService, ctx: TenantContext, did: UUID, body: Transition, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, did, "quality.post")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "quality.post")
        doc = one(
            repo, "SELECT * FROM med.quality_disposition WHERE id=:id FOR UPDATE", {"id": did}
        )
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
            raise BusinessError("QUALITY_NOT_APPROVED", "处置单必须先完成独立审核", 409)
        if ctx.principal_id == doc["created_by"]:
            raise BusinessError("MAKER_CHECKER", "制单人不能执行本单质量过账", 403)
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        item = service.item(repo, did)
        lock_goods(repo, entity, [item])
        one(
            repo,
            "SELECT id FROM med.stock_balance WHERE id=:id FOR UPDATE",
            {"id": item["balance_id"]},
        )
        row = service.source(repo, entity, item["balance_id"], doc["kind"])
        if row["quality_status"] != item["source_quality"] or row["lot_no"] != item["lot_no"]:
            raise BusinessError(
                "QUALITY_SOURCE_CHANGED", "来源质量状态或批号已变化，请重新申请", 409
            )
        pool = one(
            repo,
            "SELECT * FROM med.cost_balance WHERE id=:id FOR UPDATE",
            {"id": row["cost_balance_id"]},
        )
        row["pool_qty"] = pool["qty"]
        qty = item["qty"]
        service.check_qty(row, qty)
        release = doc["kind"] == "RELEASE"
        loss = Decimal(0) if release else loss_cost(pool, qty)
        pid = uuid4()
        action = "QUALITY_RELEASE" if release else "QUALITY_WRITE_OFF"
        result = {
            "id": str(did),
            "posting_id": str(pid),
            "state": "POSTED",
            "version": str(doc["version"] + 1),
            "kind": doc["kind"],
            "qty": format(qty, ".6f"),
            "loss_cost": format(loss, ".8f"),
            "cost_delta": format(-loss, ".8f"),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
          source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
          VALUES(:t,:p,:a,:e,'QUALITY_DISPOSITION',:id,:action,:id,:day,clock_timestamp(),:req,
            CAST(:result AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "p": pid,
                "a": ctx.principal_id,
                "e": entity,
                "id": did,
                "action": action,
                "day": day,
                "req": ctx.request_id,
                "result": json.dumps(result),
            },
        )
        if release:
            dest = balance(
                repo, ctx, entity, row["warehouse_id"], row["location_id"], item, "QUALIFIED"
            )
            move(repo, ctx, pid, item, qty, row, dest, action)
        else:
            write_off(repo, ctx, pid, item, qty, row, pool, day, action)
        repo.execute(
            """UPDATE med.quality_disposition SET state='POSTED',posting_id=:p,
          version=version+1 WHERE id=:id""",
            {"id": did, "p": pid},
        )
        audit(
            repo,
            ctx,
            "quality_disposition",
            did,
            "inventory.quality_posted",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "quality_disposition",
            did,
            doc["version"] + 1,
            "inventory.quality_posted",
            result,
        )
        return result

    return command(
        service.uow, ctx, "quality.post", str(did), key, body.model_dump(mode="json"), run
    )
