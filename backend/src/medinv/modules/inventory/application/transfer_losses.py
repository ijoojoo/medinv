import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.transfer_losses import TransferLossCreate, TransferLossReview
from medinv.modules.inventory.application.stock_movements import lock_goods
from medinv.modules.inventory.application.stock_writeoffs import loss_cost, write_off
from medinv.modules.inventory.application.transfers import TransferService
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

LOSSES = """SELECT x.*,l.line_no,l.product_snapshot->>'name' AS name,
    l.package_snapshot->>'base_unit' AS base_unit,p.result_snapshot AS result
    FROM med.transfer_loss x JOIN med.transfer_line l
      ON (l.tenant_id,l.id)=(x.tenant_id,x.line_id)
    LEFT JOIN med.posting_batch p ON (p.tenant_id,p.id)=(x.tenant_id,x.posting_id)"""


class TransferLossService:
    def __init__(self, transfers: TransferService):
        self.transfers = transfers
        self.uow = transfers.uow
        self.finance = transfers.finance

    def listing(self, ctx: TenantContext, tid: UUID, offset: int) -> dict[str, Any]:
        self.transfers.scope(ctx, tid, "transfers.read")
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"SELECT q.*,count(*) OVER() AS total FROM ({LOSSES}) q WHERE transfer_id=:id "
                "ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset",
                {"id": tid, "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(self, ctx: TenantContext, tid: UUID, lid: UUID) -> dict[str, Any]:
        self.transfers.scope(ctx, tid, "transfers.read")
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                LOSSES + " WHERE x.transfer_id=:tid AND x.id=:id",
                {"tid": tid, "id": lid},
            )
            row["version"] = str(row["version"])
            return dict(encode(row))

    @staticmethod
    def open_transfer(doc: dict[str, Any]) -> None:
        if doc["state"] not in ("IN_TRANSIT", "PART_RECEIVED"):
            raise BusinessError(
                "TRANSFER_LOSS_STATE", "仅已发出且仍有在途数量的调拨可核销损失", 409
            )

    def line(self, repo: SqlSession, tid: UUID, line_id: UUID) -> dict[str, Any]:
        lines = self.transfers.lines(repo, tid)
        for row in lines:
            if row["id"] == line_id:
                return row
        raise BusinessError("TRANSFER_LINES", "损失明细不属于当前调拨单", 422)

    @staticmethod
    def remaining(line: dict[str, Any], qty: Decimal) -> None:
        if qty > line["remaining_qty"]:
            raise BusinessError("TRANSFER_OVER_LOSS", "损失数量超过该行当前剩余在途量", 409)

    def create(
        self, ctx: TenantContext, tid: UUID, body: TransferLossCreate, key: str
    ) -> dict[str, Any]:
        entity = self.transfers.scope(ctx, tid, "transfers.loss.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "transfers.loss.write")
            doc = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
            version(doc, body.expected_version)
            self.open_transfer(doc)
            line = self.line(repo, tid, body.line_id)
            qty = self.transfers.qty(body.qty, line["qty_scale"])
            if line["lot_no"] != body.lot_no:
                raise BusinessError("TRANSFER_LOT_MISMATCH", "确认批号与调拨批次不一致", 409)
            self.remaining(line, qty)
            if repo.rows(
                """SELECT id FROM med.transfer_loss WHERE line_id=:id
              AND state IN ('DRAFT','APPROVED')""",
                {"id": body.line_id},
            ):
                raise BusinessError("TRANSFER_LOSS_PENDING", "该调拨行已有待处理损失申请", 409)
            lid = uuid4()
            number = document_number("TRL", lid)
            repo.execute(
                """INSERT INTO med.transfer_loss(tenant_id,id,transfer_id,line_id,
              number,qty,lot_no,reason,evidence,created_by)
              VALUES(:t,:id,:tr,:line,:n,:q,:lot,:reason,:evidence,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": lid,
                    "tr": tid,
                    "line": body.line_id,
                    "n": number,
                    "q": qty,
                    "lot": body.lot_no,
                    "reason": body.reason,
                    "evidence": body.evidence,
                    "a": ctx.principal_id,
                },
            )
            result = {
                "id": str(lid),
                "transfer_id": str(tid),
                "number": number,
                "state": "DRAFT",
                "version": "1",
            }
            audit(
                repo,
                ctx,
                "transfer_loss",
                lid,
                "inventory.transfer_loss_created",
                {**body.model_dump(mode="json"), **result},
            )
            return result

        return self.finance.permanent(
            ctx,
            "transfers.loss.create",
            str(tid),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def transition(
        self,
        ctx: TenantContext,
        tid: UUID,
        lid: UUID,
        body: Transition,
        key: str,
        review: bool = False,
    ) -> dict[str, Any]:
        permission = "transfers.loss.review" if review else "transfers.loss.write"
        entity = self.transfers.scope(ctx, tid, permission)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, permission)
            transfer = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
            doc = one(
                repo,
                "SELECT * FROM med.transfer_loss WHERE id=:id AND transfer_id=:tr FOR UPDATE",
                {"id": lid, "tr": tid},
            )
            version(doc, body.expected_version)
            if doc["state"] not in (("DRAFT",) if review else ("DRAFT", "APPROVED")):
                raise BusinessError("INVALID_STATE", "仅未核销的损失申请可审核或取消", 409)
            state = "CANCELLED"
            if review:
                assert isinstance(body, TransferLossReview)
                if doc["created_by"] == ctx.principal_id:
                    raise BusinessError("MAKER_CHECKER", "损失申请必须由其他人员独立审核", 403)
                if body.decision == "APPROVE":
                    self.open_transfer(transfer)
                    self.remaining(self.line(repo, tid, doc["line_id"]), doc["qty"])
                    state = "APPROVED"
                repo.execute(
                    """UPDATE med.transfer_loss SET reviewed_by=:a,
                  reviewed_at=clock_timestamp(),review_note=:reason WHERE id=:id""",
                    {"id": lid, "a": ctx.principal_id, "reason": body.reason},
                )
            repo.execute(
                """UPDATE med.transfer_loss SET state=:state,version=version+1,
              cancel_note=CASE WHEN CAST(:state AS varchar)='CANCELLED' THEN :reason ELSE NULL END
              WHERE id=:id""",
                {"id": lid, "state": state, "reason": body.reason},
            )
            result = {
                "id": str(lid),
                "transfer_id": str(tid),
                "state": state,
                "version": str(doc["version"] + 1),
            }
            audit(
                repo,
                ctx,
                "transfer_loss",
                lid,
                "inventory.transfer_loss_reviewed"
                if review
                else "inventory.transfer_loss_cancelled",
                {**result, "reason": body.reason},
            )
            return result

        return command(
            self.uow,
            ctx,
            "transfers.loss.review" if review else "transfers.loss.cancel",
            str(lid),
            key,
            {"transfer_id": str(tid), **body.model_dump(mode="json")},
            run,
        )

    def post(
        self, ctx: TenantContext, tid: UUID, lid: UUID, body: Transition, key: str
    ) -> dict[str, Any]:
        entity = self.transfers.scope(ctx, tid, "transfers.loss.post")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "transfers.loss.post")
            transfer = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
            doc = one(
                repo,
                "SELECT * FROM med.transfer_loss WHERE id=:id AND transfer_id=:tr FOR UPDATE",
                {"id": lid, "tr": tid},
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
                raise BusinessError("TRANSFER_LOSS_NOT_APPROVED", "须先完成独立审核再核销损失", 409)
            if doc["created_by"] == ctx.principal_id:
                raise BusinessError("MAKER_CHECKER", "制单人不能核销本人损失申请", 403)
            self.open_transfer(transfer)
            day = self.finance.today(repo)
            self.finance.period(repo, ctx, entity, day)
            lines = self.transfers.lines(repo, tid)
            line = next(r for r in lines if r["id"] == doc["line_id"])
            self.remaining(line, doc["qty"])
            lock_goods(repo, entity, [line])
            goods = one(
                repo,
                """SELECT s.serial_required,s.qty_scale,p.restricted_category
              FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
              WHERE s.id=:id""",
                {"id": line["sku_id"]},
            )
            if goods["serial_required"] or goods["restricted_category"] is not None:
                raise BusinessError("TRANSFER_LOSS_UNSUPPORTED", "仅支持普通批次在途损失", 409)
            qty = self.transfers.qty(format(doc["qty"], "f"), goods["qty_scale"])
            if doc["lot_no"] != line["lot_no"]:
                raise BusinessError("TRANSFER_LOT_MISMATCH", "批号已变化，请重新核对申请", 409)
            source = one(
                repo,
                """SELECT b.* FROM med.stock_balance b
              JOIN med.warehouse w ON (w.tenant_id,w.id,w.entity_id)=
                (b.tenant_id,b.warehouse_id,b.entity_id)
              WHERE b.entity_id=:e AND b.owner_id=:e AND b.warehouse_id=:w
                AND b.location_id=:l AND b.sku_id=:s AND b.lot_id=:lot
                AND b.quality_status='QUALIFIED' AND w.kind='TRANSIT' FOR UPDATE OF b""",
                {
                    "e": entity,
                    "w": transfer["transit_warehouse_id"],
                    "l": transfer["transit_location_id"],
                    "s": line["sku_id"],
                    "lot": line["lot_id"],
                },
            )
            pool = one(
                repo,
                """SELECT * FROM med.cost_balance WHERE entity_id=:e AND owner_id=:e
              AND sku_id=:s AND lot_id=:lot FOR UPDATE""",
                {"e": entity, "s": line["sku_id"], "lot": line["lot_id"]},
            )
            amount = loss_cost(pool, qty)
            received = sum((r["received_qty"] for r in lines), Decimal(0))
            lost = sum((r["lost_qty"] for r in lines), Decimal(0)) + qty
            total = sum((r["qty"] for r in lines), Decimal(0))
            state = self.transfers.receipt_state(received, lost, total)
            pid = uuid4()
            result = {
                "id": str(lid),
                "transfer_id": str(tid),
                "state": "POSTED",
                "version": str(doc["version"] + 1),
                "posting_id": str(pid),
                "transfer_state": state,
                "transfer_version": str(transfer["version"] + 1),
                "qty": format(qty, ".6f"),
                "loss_cost": format(amount, ".8f"),
                "cost_delta": format(-amount, ".8f"),
            }
            repo.execute(
                """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,
              source_type,source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
              VALUES(:t,:p,:a,:e,'TRANSFER_LOSS',:id,'TRANSFER_LOSS',:id,:day,clock_timestamp(),
              :req,CAST(:result AS jsonb))""",
                {
                    "t": ctx.tenant_id,
                    "p": pid,
                    "a": ctx.principal_id,
                    "e": entity,
                    "id": lid,
                    "day": day,
                    "req": ctx.request_id,
                    "result": json.dumps(result),
                },
            )
            write_off(repo, ctx, pid, {**line, "id": lid}, qty, source, pool, day, "TRANSFER_LOSS")
            repo.execute(
                """UPDATE med.transfer_loss SET state='POSTED',version=version+1,
              posting_id=:p WHERE id=:id""",
                {"id": lid, "p": pid},
            )
            repo.execute(
                "UPDATE med.transfer SET state=:state,version=version+1 WHERE id=:id",
                {"id": tid, "state": state},
            )
            audit(
                repo,
                ctx,
                "transfer_loss",
                lid,
                "inventory.transfer_loss_posted",
                {**result, "reason": body.reason},
            )
            event(
                repo,
                ctx,
                uuid4(),
                "transfer",
                tid,
                transfer["version"] + 1,
                "inventory.transfer_loss_posted",
                result,
            )
            return result

        return command(
            self.uow,
            ctx,
            "transfers.loss.post",
            str(lid),
            key,
            {"transfer_id": str(tid), **body.model_dump(mode="json")},
            run,
        )
