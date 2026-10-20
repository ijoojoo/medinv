from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.returns.api.schemas import RefundCreate
from medinv.modules.returns.application.service import ReturnService
from medinv.modules.settlement.api.schemas import ReverseRequest
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def listing(service: ReturnService, ctx: TenantContext, rid: UUID, offset: int) -> dict[str, Any]:
    service.return_scope(ctx, rid, "finance.read")
    with service.uow.open(ctx, readonly=True) as session:
        repo = SqlSession(session)
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id", {"id": rid})
        rows = repo.rows(
            """SELECT f.*,c.number AS cash_number,a.name AS account_name,v.reason AS void_reason,
          count(*) OVER() AS total FROM med.cash_refund f
          JOIN med.cash_document c ON (c.tenant_id,c.id)=(f.tenant_id,f.cash_document_id)
          JOIN med.cash_account a ON (a.tenant_id,a.id)=(f.tenant_id,f.account_id)
          LEFT JOIN med.cash_refund_void v ON (v.tenant_id,v.refund_id)=(f.tenant_id,f.id)
          WHERE f.return_document_id=:id ORDER BY f.created_at DESC,f.id LIMIT 50 OFFSET :offset""",
            {"id": rid, "offset": offset},
        )
        refunded = one(
            repo,
            """SELECT coalesce(sum(amount),0) AS amount FROM med.cash_refund
          WHERE return_document_id=:id AND state='CONFIRMED'""",
            {"id": rid},
        )["amount"]
        return dict(
            encode(
                {
                    "items": rows,
                    "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
                    "remaining_amount": doc["gross_amount"] - refunded,
                }
            )
        )


def options(service: ReturnService, ctx: TenantContext, rid: UUID, offset: int) -> dict[str, Any]:
    service.return_scope(ctx, rid, "finance.read")
    with service.uow.open(ctx, readonly=True) as session:
        repo = SqlSession(session)
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id", {"id": rid})
        rows = repo.rows(
            """SELECT c.*,a.name AS account_name,
          c.amount-c.allocated_amount-c.refunded_amount AS remaining_amount,count(*) OVER() AS total
          FROM med.cash_document c JOIN med.cash_account a ON
              (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
          WHERE c.entity_id=:e AND c.party_id=:party AND c.state='CONFIRMED'
            AND c.amount>c.allocated_amount+c.refunded_amount
            AND EXISTS(SELECT 1 FROM med.settlement_allocation sa WHERE
              (sa.tenant_id,sa.cash_document_id)=(c.tenant_id,c.id)
              AND coalesce(sa.receivable_id,sa.payable_id)=:o)
          ORDER BY c.created_at DESC,c.id LIMIT 50 OFFSET :offset""",
            {
                "e": doc["entity_id"],
                "party": doc["party_id"],
                "o": doc["receivable_id"] or doc["payable_id"],
                "offset": offset,
            },
        )
        return {
            "items": encode([{**r, "version": str(r["version"])} for r in rows]),
            "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
        }


def create(
    service: ReturnService, ctx: TenantContext, body: RefundCreate, key: str
) -> dict[str, Any]:
    settlement = SettlementService(service.uow)
    entity = settlement.scope(ctx, body.cash_document_id, "finance.reverse")
    service.return_scope(ctx, body.return_document_id, "finance.reverse")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, "finance.reverse")
        doc = one(
            repo,
            "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE",
            {"id": body.return_document_id},
        )
        if doc["kind"] == "RETAIL":
            raise BusinessError("RETAIL_REFUND_REQUIRED", "请使用零售退货的原现金退款入口", 422)
        cash = one(
            repo,
            "SELECT * FROM med.cash_document WHERE id=:id FOR UPDATE",
            {"id": body.cash_document_id},
        )
        version(cash, body.expected_cash_version)
        direction = "PAYMENT" if cash["direction"] == "RECEIPT" else "RECEIPT"
        if (
            doc["entity_id"],
            doc["party_id"],
            body.entity_id,
            body.party_id,
            body.account_id,
            body.direction,
        ) != (
            entity,
            cash["party_id"],
            entity,
            cash["party_id"],
            cash["account_id"],
            direction,
        ) or (doc["kind"] == "SALES") != (cash["direction"] == "RECEIPT"):
            raise BusinessError(
                "REFUND_SCOPE_MISMATCH", "退款必须同主体、同往来并退回原资金账户", 422
            )
        if doc["state"] != "POSTED" or cash["state"] != "CONFIRMED":
            raise BusinessError("REFUND_STATE", "必须先完成退货过账，且原款项仍有效", 409)
        if not repo.rows(
            """SELECT id FROM med.settlement_allocation WHERE cash_document_id=:c
          AND coalesce(receivable_id,payable_id)=:o LIMIT 1""",
            {"c": cash["id"], "o": doc["receivable_id"] or doc["payable_id"]},
        ):
            raise BusinessError("REFUND_SOURCE", "该款项未曾核销原单，不能作为本退货退款来源", 422)
        refunded = one(
            repo,
            """SELECT coalesce(sum(amount),0) AS amount FROM med.cash_refund
          WHERE return_document_id=:r AND state='CONFIRMED'""",
            {"r": doc["id"]},
        )["amount"]
        amount = Decimal(body.amount)
        if (
            amount > cash["amount"] - cash["allocated_amount"] - cash["refunded_amount"]
            or amount > doc["gross_amount"] - refunded
        ):
            raise BusinessError("REFUND_EXCEEDED", "退款超过原款项可退余额或退货剩余金额", 409)
        if body.occurred_on > service.today(repo):
            raise BusinessError("FUTURE_CASH_DATE", "不能登记未来发生的退款", 422)
        service.period(repo, ctx, entity, body.occurred_on)
        params = {
            "e": entity,
            "account": cash["account_id"],
            "direction": direction,
            "ref": body.reference,
        }
        if repo.rows(
            """SELECT id FROM med.cash_refund WHERE entity_id=:e AND account_id=:account
          AND direction=:direction AND reference=:ref AND state='CONFIRMED' UNION ALL
          SELECT id FROM med.cash_document WHERE entity_id=:e AND account_id=:account
          AND direction=:direction AND reference=:ref AND state='CONFIRMED'""",
            params,
        ):
            raise BusinessError(
                "CASH_REFERENCE_EXISTS", "该账户与方向的凭据已登记，请核查原记录", 409
            )
        fid = uuid4()
        number = document_number("RF", fid)
        repo.execute(
            """INSERT INTO med.cash_refund(tenant_id,id,entity_id,cash_document_id,
         
              return_document_id,account_id,direction,number,reference,amount,occurred_on,note,created_by)
          VALUES(:t,:id,:entity_id,:cash_document_id,:return_document_id,:account_id,:direction,
          :n,:reference,:amount,:occurred_on,:note,:a)""",
            {
                **body.model_dump(),
                "t": ctx.tenant_id,
                "id": fid,
                "n": number,
                "a": ctx.principal_id,
                "amount": amount,
            },
        )
        repo.execute(
            """UPDATE med.cash_document SET refunded_amount=refunded_amount+:v,
          version=version+1 WHERE id=:id""",
            {"v": amount, "id": cash["id"]},
        )
        result = {
            "id": str(fid),
            "number": number,
            "remaining_amount": format(doc["gross_amount"] - refunded - amount, ".2f"),
        }
        audit(repo, ctx, "refund", fid, "finance.refund_recorded", body.model_dump(mode="json"))
        event(repo, ctx, uuid4(), "refund", fid, 1, "finance.refund_recorded", result)
        return result

    return service.permanent(
        ctx,
        "finance.refund",
        str(body.return_document_id),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )


def void(
    service: ReturnService, ctx: TenantContext, fid: UUID, body: ReverseRequest, key: str
) -> dict[str, Any]:
    ctx.require("finance.reverse")
    with service.uow.open(ctx, readonly=True) as session:
        found = one(SqlSession(session), "SELECT * FROM med.cash_refund WHERE id=:id", {"id": fid})
    ctx.require("finance.reverse", found["entity_id"])

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, found["entity_id"], "finance.reverse")
        service.period(repo, ctx, found["entity_id"], service.today(repo))
        row = one(repo, "SELECT * FROM med.cash_refund WHERE id=:id FOR UPDATE", {"id": fid})
        if row["state"] == "VOIDED":
            raise BusinessError("REFUND_ALREADY_VOIDED", "该退款登记已经作废", 409)
        cash = one(
            repo,
            "SELECT * FROM med.cash_document WHERE id=:id FOR UPDATE",
            {"id": row["cash_document_id"]},
        )
        if cash["state"] != "CONFIRMED" or cash["refunded_amount"] < row["amount"]:
            raise BusinessError("REFUND_INCONSISTENT", "原款项退款余额异常", 409)
        repo.execute(
            """INSERT INTO med.cash_refund_void(tenant_id,id,refund_id,reason,created_by)
          VALUES(:t,:id,:r,:reason,:a)""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "r": fid,
                "reason": body.reason,
                "a": ctx.principal_id,
            },
        )
        repo.execute("UPDATE med.cash_refund SET state='VOIDED' WHERE id=:id", {"id": fid})
        repo.execute(
            """UPDATE med.cash_document SET refunded_amount=refunded_amount-:v,
          version=version+1 WHERE id=:id""",
            {"id": cash["id"], "v": row["amount"]},
        )
        result = {"id": str(fid), "state": "VOIDED"}
        audit(repo, ctx, "refund", fid, "finance.refund_voided", body.model_dump(mode="json"))
        event(repo, ctx, uuid4(), "refund", fid, 2, "finance.refund_voided", result)
        return result

    return service.permanent(
        ctx,
        "finance.refund_void",
        str(fid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )
