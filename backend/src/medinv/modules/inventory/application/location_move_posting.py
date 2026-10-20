import json
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.location_moves import LocationMovePost
from medinv.modules.inventory.application.location_moves import LocationMoveService
from medinv.modules.inventory.application.stock_movements import balance, lock_goods, move
from medinv.modules.procurement.application.service import one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: LocationMoveService, ctx: TenantContext, did: UUID, body: LocationMovePost, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, did, "transfers.dispatch")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "transfers.dispatch")
        doc = one(repo, "SELECT * FROM med.location_move WHERE id=:id FOR UPDATE", {"id": did})
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
            raise BusinessError("MOVE_NOT_APPROVED", "移库单必须先完成独立审核", 409)
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        item = service.item(repo, did)
        lock_goods(repo, entity, [item])
        one(
            repo,
            "SELECT id FROM med.stock_balance WHERE id=:id FOR UPDATE",
            {"id": item["balance_id"]},
        )
        row = service.source(repo, entity, item["balance_id"])
        if row["lot_no"] != item["lot_no"] or body.lot_no != item["lot_no"]:
            raise BusinessError("MOVE_LOT_MISMATCH", "实物批号与原申请不一致", 409)
        service.destination(repo, row, doc["destination_location_id"])
        qty = item["qty"]
        service.check_qty(row, qty)
        pid = uuid4()
        action = "LOCATION_MOVE"
        result = {
            "id": str(did),
            "posting_id": str(pid),
            "state": "POSTED",
            "version": str(doc["version"] + 1),
            "qty": format(qty, ".6f"),
            "cost_delta": "0.00000000",
            "source_balance_id": str(row["id"]),
            "destination_location_id": str(doc["destination_location_id"]),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
          source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
          VALUES(:t,:p,:a,:e,'LOCATION_MOVE',:id,:action,:id,:day,clock_timestamp(),:req,
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
        dest = balance(
            repo,
            ctx,
            entity,
            row["warehouse_id"],
            doc["destination_location_id"],
            item,
            "QUALIFIED",
        )
        move(repo, ctx, pid, item, qty, row, dest, action)
        repo.execute(
            """UPDATE med.location_move SET state='POSTED',posting_id=:p,
          version=version+1 WHERE id=:id""",
            {"id": did, "p": pid},
        )
        audit(
            repo,
            ctx,
            "location_move",
            did,
            "inventory.location_move_posted",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "location_move",
            did,
            doc["version"] + 1,
            "inventory.location_move_posted",
            result,
        )
        return result

    return command(
        service.uow, ctx, "location_move.post", str(did), key, body.model_dump(mode="json"), run
    )
