import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.transfers import TransferReceive
from medinv.modules.inventory.application.counts import BALANCES
from medinv.modules.inventory.application.stock_movements import balance, lock_goods, move
from medinv.modules.inventory.application.transfers import TransferService
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def posting(
    repo: SqlSession,
    ctx: TenantContext,
    doc: dict[str, Any],
    pid: UUID,
    segment: UUID,
    action: str,
    day: Any,
    result: dict[str, Any],
) -> None:
    repo.execute(
        """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
      source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
      VALUES(:t,:p,:a,:e,'TRANSFER',:tr,:action,:segment,:day,clock_timestamp(),:req,
      CAST(:result AS jsonb))""",
        {
            "t": ctx.tenant_id,
            "p": pid,
            "a": ctx.principal_id,
            "e": doc["entity_id"],
            "tr": doc["id"],
            "action": action,
            "segment": segment,
            "day": day,
            "req": ctx.request_id,
            "result": json.dumps(result),
        },
    )


def notify(
    repo: SqlSession,
    ctx: TenantContext,
    tid: UUID,
    action: str,
    result: dict[str, Any],
    reason: str,
) -> None:
    audit(repo, ctx, "transfer", tid, action, {**result, "reason": reason})
    event(repo, ctx, uuid4(), "transfer", tid, int(result["version"]), action, result)


def dispatch(
    service: TransferService, ctx: TenantContext, tid: UUID, body: Transition, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, tid, "transfers.dispatch")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "transfers.dispatch")
        doc = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
        if doc["dispatch_posting_id"]:
            return dict(
                one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["dispatch_posting_id"]},
                )["result_snapshot"]
            )
        version(doc, body.expected_version)
        if doc["state"] != "APPROVED":
            raise BusinessError("TRANSFER_NOT_APPROVED", "调拨须先完成独立审核", 409)
        service.warehouses(
            repo,
            entity,
            doc["source_warehouse_id"],
            doc["destination_warehouse_id"],
            doc["destination_location_id"],
        )
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        lines = service.lines(repo, tid)
        lock_goods(repo, entity, lines)
        repo.rows(
            "SELECT id FROM med.stock_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
            {"ids": sorted(r["balance_id"] for r in lines)},
        )
        sources = {
            r["id"]: r
            for r in repo.rows(
                BALANCES + " WHERE b.id=ANY(:ids) ORDER BY b.id",
                {"ids": sorted(r["balance_id"] for r in lines)},
            )
        }
        repo.execute(
            """INSERT INTO med.warehouse
          (tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
          VALUES(:t,:id,:a,:a,:e,'SYS-TRANSIT','TRANSIT','调拨在途库')
          ON CONFLICT(tenant_id,entity_id,code) DO NOTHING""",
            {"t": ctx.tenant_id, "id": uuid4(), "a": ctx.principal_id, "e": entity},
        )
        warehouse = one(
            repo,
            "SELECT * FROM med.warehouse WHERE entity_id=:e AND code='SYS-TRANSIT'",
            {"e": entity},
        )
        if warehouse["kind"] != "TRANSIT":
            raise BusinessError("TRANSIT_CONFIG", "系统在途仓编码被占用，请修正配置", 409)
        loc = uuid4()
        repo.execute(
            """INSERT INTO med.location(tenant_id,id,created_by,updated_by,warehouse_id,code)
          VALUES(:t,:id,:a,:a,:w,:code)""",
            {
                "t": ctx.tenant_id,
                "id": loc,
                "a": ctx.principal_id,
                "w": warehouse["id"],
                "code": doc["number"],
            },
        )
        pid = uuid4()
        result = {
            "id": str(tid),
            "posting_id": str(pid),
            "state": "IN_TRANSIT",
            "version": str(doc["version"] + 1),
        }
        posting(repo, ctx, doc, pid, tid, "TRANSFER_DISPATCH", day, result)
        for line in lines:
            source = sources[line["balance_id"]]
            service.eligible(repo, source)
            dest = balance(repo, ctx, entity, warehouse["id"], loc, line, "QUALIFIED")
            move(repo, ctx, pid, line, line["qty"], source, dest, "TRANSFER_DISPATCH")
        repo.execute(
            """UPDATE med.transfer SET state='IN_TRANSIT',version=version+1,
          dispatch_posting_id=:p,transit_warehouse_id=:w,transit_location_id=:l WHERE id=:id""",
            {"id": tid, "p": pid, "w": warehouse["id"], "l": loc},
        )
        notify(repo, ctx, tid, "inventory.transfer_dispatched", result, body.reason)
        return result

    return command(
        service.uow, ctx, "transfers.dispatch", str(tid), key, body.model_dump(mode="json"), run
    )


def receive(
    service: TransferService, ctx: TenantContext, tid: UUID, body: TransferReceive, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, tid, "transfers.receive")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "transfers.receive")
        doc = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
        version(doc, body.expected_version)
        if doc["state"] not in ("IN_TRANSIT", "PART_RECEIVED"):
            raise BusinessError("INVALID_STATE", "仅已发出且未收齐的调拨可收货", 409)
        service.warehouses(
            repo,
            entity,
            doc["source_warehouse_id"],
            doc["destination_warehouse_id"],
            doc["destination_location_id"],
        )
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        lines = service.lines(repo, tid)
        selected = {r.line_id: r for r in body.lines}
        if not set(selected).issubset({r["id"] for r in lines}):
            raise BusinessError("TRANSFER_LINES", "收货明细不属于当前调拨单", 422)
        lock_goods(repo, entity, lines)
        sources = {
            r["id"]: r
            for r in repo.rows(
                BALANCES + " WHERE b.id=ANY(:ids) ORDER BY b.id",
                {"ids": sorted(r["balance_id"] for r in lines)},
            )
        }
        received = sum((r["received_qty"] for r in lines), Decimal(0))
        lost = sum((r["lost_qty"] for r in lines), Decimal(0))
        total = sum((r["qty"] for r in lines), Decimal(0))
        for line in lines:
            if line["id"] not in selected:
                continue
            reading = selected[line["id"]]
            qty = service.qty(reading.qty, line["qty_scale"])
            if reading.lot_no != line["lot_no"]:
                raise BusinessError("TRANSFER_LOT_MISMATCH", "实物批号与调拨批次不一致", 409)
            if qty > line["remaining_qty"]:
                raise BusinessError("TRANSFER_OVER_RECEIVE", "本次收货超过该行剩余在途数量", 409)
            if reading.quality_status == "QUALIFIED":
                service.eligible(repo, sources[line["balance_id"]])
            received += qty
        pid, rid = uuid4(), uuid4()
        number = document_number("TRR", rid)
        state = service.receipt_state(received, lost, total)
        result = {
            "id": str(tid),
            "receipt_id": str(rid),
            "number": number,
            "posting_id": str(pid),
            "state": state,
            "version": str(doc["version"] + 1),
        }
        posting(repo, ctx, doc, pid, rid, "TRANSFER_RECEIVE", day, result)
        repo.execute(
            """INSERT INTO med.transfer_receipt
          (tenant_id,id,transfer_id,number,posting_id,note,created_by)
          VALUES(:t,:id,:tr,:n,:p,:note,:a)""",
            {
                "t": ctx.tenant_id,
                "id": rid,
                "tr": tid,
                "n": number,
                "p": pid,
                "note": body.reason,
                "a": ctx.principal_id,
            },
        )
        for line in lines:
            if line["id"] not in selected:
                continue
            reading = selected[line["id"]]
            qty = Decimal(reading.qty)
            source = balance(
                repo,
                ctx,
                entity,
                doc["transit_warehouse_id"],
                doc["transit_location_id"],
                line,
                "QUALIFIED",
            )
            dest = balance(
                repo,
                ctx,
                entity,
                doc["destination_warehouse_id"],
                doc["destination_location_id"],
                line,
                reading.quality_status,
            )
            move(repo, ctx, pid, line, qty, source, dest, "TRANSFER_RECEIVE")
            repo.execute(
                """INSERT INTO med.transfer_receipt_line
              (tenant_id,id,receipt_id,transfer_id,line_id,qty,quality_status,created_by)
              VALUES(:t,:id,:r,:tr,:line,:q,:quality,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "r": rid,
                    "tr": tid,
                    "line": line["id"],
                    "q": qty,
                    "quality": reading.quality_status,
                    "a": ctx.principal_id,
                },
            )
        repo.execute(
            "UPDATE med.transfer SET state=:state,version=version+1 WHERE id=:id",
            {"id": tid, "state": state},
        )
        notify(repo, ctx, tid, "inventory.transfer_received", result, body.reason)
        return result

    return service.finance.permanent(
        ctx,
        "transfers.receive",
        str(tid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )
