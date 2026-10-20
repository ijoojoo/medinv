from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.domain.decimal_values import money_for_posting
from medinv.modules.procurement.application.service import one, version
from medinv.modules.settlement.api.schemas import AllocateRequest, ReverseRequest
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def credit_delta(
    repo: SqlSession, ctx: TenantContext, cash: dict[str, Any], delta: Decimal
) -> None:
    if cash["direction"] != "RECEIPT":
        return
    credit = one(
        repo,
        """SELECT receivable FROM med.credit_account
      WHERE entity_id=:e AND customer_id=:c FOR UPDATE""",
        {"e": cash["entity_id"], "c": cash["party_id"]},
    )
    try:
        value = money_for_posting(credit["receivable"] + delta)
    except ValueError as exc:
        raise BusinessError("CREDIT_INCONSISTENT", "客户应收余额异常，核销未提交", 409) from exc
    repo.execute(
        """UPDATE med.credit_account SET receivable=:v,version=version+1,
      updated_by=:a,updated_at=clock_timestamp() WHERE entity_id=:e AND customer_id=:c""",
        {
            "v": value,
            "a": ctx.principal_id,
            "e": cash["entity_id"],
            "c": cash["party_id"],
        },
    )


def allocate(
    service: SettlementService, ctx: TenantContext, body: AllocateRequest, key: str
) -> dict[str, Any]:
    cid = body.cash_document_id
    entity = service.scope(ctx, cid, "finance.allocate")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, "finance.allocate")
        service.period(repo, ctx, entity, service.today(repo))
        cash = one(repo, "SELECT * FROM med.cash_document WHERE id=:id FOR UPDATE", {"id": cid})
        version(cash, body.expected_cash_version)
        if cash["state"] != "CONFIRMED":
            raise BusinessError("CASH_VOIDED", "已作废款项不能核销", 409)
        kind = "AR" if cash["direction"] == "RECEIPT" else "AP"
        total = sum((Decimal(entry.amount) for entry in body.allocations), Decimal(0))
        if total > cash["amount"] - cash["allocated_amount"] - cash["refunded_amount"]:
            raise BusinessError("CASH_EXCEEDED", "核销合计超出款项未核销金额", 409)
        for entry in body.allocations:
            item = one(
                repo,
                "SELECT * FROM med.settlement_item WHERE id=:id AND kind=:kind",
                {"id": entry.settlement_item_id, "kind": kind},
            )
            if (item["entity_id"], item["party_id"], item["currency"]) != (
                entity,
                cash["party_id"],
                cash["currency"],
            ):
                raise BusinessError(
                    "SETTLEMENT_SCOPE_MISMATCH", "核销必须同主体、同往来单位及币种", 422
                )
            version(item, entry.expected_item_version)
            if Decimal(entry.amount) > item["outstanding_amount"]:
                raise BusinessError("OBLIGATION_EXCEEDED", "核销金额超出单据未结余额", 409)
        ids = []
        for entry in body.allocations:
            aid = uuid4()
            ids.append(str(aid))
            repo.execute(
                """INSERT INTO med.settlement_allocation
              (tenant_id,id,cash_document_id,receivable_id,payable_id,amount,created_by)
              VALUES(:t,:id,:cash,:ar,:ap,:amount,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": aid,
                    "cash": cid,
                    "a": ctx.principal_id,
                    "ar": entry.settlement_item_id if kind == "AR" else None,
                    "ap": entry.settlement_item_id if kind == "AP" else None,
                    "amount": Decimal(entry.amount),
                },
            )
        credit_delta(repo, ctx, cash, -total)
        repo.execute(
            """UPDATE med.cash_document SET allocated_amount=allocated_amount+:v,
                version=version+1 WHERE id=:id""",
            {"v": total, "id": cid},
        )
        result = {
            "allocation_ids": ids,
            "cash_document_id": str(cid),
            "new_cash_version": str(cash["version"] + 1),
            "remaining_amount": format(
                cash["amount"] - cash["allocated_amount"] - cash["refunded_amount"] - total, ".2f"
            ),
        }
        audit(repo, ctx, "cash_document", cid, "finance.allocated", body.model_dump(mode="json"))
        event(
            repo,
            ctx,
            uuid4(),
            "cash_document",
            cid,
            cash["version"] + 1,
            "finance.allocated",
            result,
        )
        return result

    return service.permanent(
        ctx,
        "finance.allocate",
        str(cid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )


def reverse(
    service: SettlementService, ctx: TenantContext, aid: UUID, body: ReverseRequest, key: str
) -> dict[str, Any]:
    ctx.require("finance.reverse")
    with service.uow.open(ctx, readonly=True) as session:
        cid = UUID(
            str(
                one(
                    SqlSession(session),
                    "SELECT cash_document_id FROM med.settlement_allocation WHERE id=:id",
                    {"id": aid},
                )["cash_document_id"]
            )
        )
    entity = service.scope(ctx, cid, "finance.reverse")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, "finance.reverse")
        service.period(repo, ctx, entity, service.today(repo))
        cash = one(repo, "SELECT * FROM med.cash_document WHERE id=:id FOR UPDATE", {"id": cid})
        entry = one(repo, "SELECT * FROM med.settlement_allocation WHERE id=:id", {"id": aid})
        if repo.rows("SELECT id FROM med.settlement_reversal WHERE allocation_id=:id", {"id": aid}):
            raise BusinessError("ALLOCATION_REVERSED", "该核销已经撤销，请刷新记录", 409)
        if cash["state"] != "CONFIRMED" or cash["allocated_amount"] < entry["amount"]:
            raise BusinessError("CASH_INCONSISTENT", "款项余额或状态异常", 409)
        rid = uuid4()
        repo.execute(
            """INSERT INTO med.settlement_reversal(tenant_id,id,allocation_id,reason,created_by)
          VALUES(:t,:id,:allocation,:reason,:a)""",
            {
                "t": ctx.tenant_id,
                "id": rid,
                "allocation": aid,
                "reason": body.reason,
                "a": ctx.principal_id,
            },
        )
        # Restore risk even if the customer's limit has subsequently been reduced.
        credit_delta(repo, ctx, cash, entry["amount"])
        repo.execute(
            """UPDATE med.cash_document SET allocated_amount=allocated_amount-:v,
                version=version+1 WHERE id=:id""",
            {"v": entry["amount"], "id": cid},
        )
        result = {
            "id": str(rid),
            "allocation_id": str(aid),
            "cash_document_id": str(cid),
            "new_cash_version": str(cash["version"] + 1),
            "amount": format(entry["amount"], ".2f"),
        }
        audit(
            repo,
            ctx,
            "cash_document",
            cid,
            "finance.allocation_reversed",
            {"allocation_id": str(aid), **body.model_dump(mode="json")},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "cash_document",
            cid,
            cash["version"] + 1,
            "finance.allocation_reversed",
            result,
        )
        return result

    return service.permanent(
        ctx,
        "finance.reverse",
        str(aid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )
