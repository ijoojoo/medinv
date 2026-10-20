"""Cash refunds against accepted retail returns; original receipts remain immutable."""

from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.retail.api.schemas import RetailCashConfirmation
from medinv.modules.returns.application.service import ReturnService
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def require_retail(doc: dict[str, Any]) -> None:
    if doc["kind"] != "RETAIL":
        raise BusinessError("RETAIL_RETURN_REQUIRED", "该接口仅处理已完成零售单的退货退款", 422)


def detail(service: ReturnService, ctx: TenantContext, rid: UUID) -> dict[str, Any]:
    service.return_scope(ctx, rid, "returns.read")
    with service.uow.open(ctx, readonly=True) as session:
        repo = SqlSession(session)
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id", {"id": rid})
        require_retail(doc)
        receipt = one(
            repo,
            """SELECT p.*,a.name AS account_name FROM med.retail_payment p
            JOIN med.cash_account a ON (a.tenant_id,a.id)=(p.tenant_id,p.account_id)
            WHERE p.sale_id=:id AND p.direction='RECEIPT'""",
            {"id": doc["retail_sale_id"]},
        )
        records = repo.rows(
            "SELECT * FROM med.retail_return_refund WHERE return_id=:id", {"id": rid}
        )
        return dict(
            encode(
                {
                    "receipt": receipt,
                    "refund": records[0] if records else None,
                    "remaining_amount": doc["gross_amount"] - sum(r["amount"] for r in records),
                    "version": str(doc["version"]),
                    "state": doc["state"],
                }
            )
        )


def create(
    service: ReturnService, ctx: TenantContext, rid: UUID, body: RetailCashConfirmation, key: str
) -> dict[str, Any]:
    entity = service.return_scope(ctx, rid, "retail.refund")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, "retail.refund")
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid})
        require_retail(doc)
        version(doc, body.expected_version)
        if doc["state"] != "POSTED":
            raise BusinessError("REFUND_STATE", "请先完成隔离收货、独立验收和退货入库", 409)
        if doc["gross_amount"] <= 0:
            raise BusinessError("NO_REFUND_DUE", "本次退货金额为零，无需现金退款", 409)
        if repo.rows("SELECT id FROM med.retail_return_refund WHERE return_id=:id", {"id": rid}):
            raise BusinessError(
                "REFUND_EXISTS", "本次退货已登记退款，请核对原记录，勿重复退款", 409
            )
        sale = one(
            repo,
            "SELECT * FROM med.retail_sale WHERE id=:id FOR UPDATE",
            {"id": doc["retail_sale_id"]},
        )
        if sale["state"] != "COMPLETED" or sale["posting_id"] != doc["source_posting_id"]:
            raise BusinessError("REFUND_SOURCE", "原零售单未完成或来源不一致", 409)
        receipt = one(
            repo,
            """SELECT * FROM med.retail_payment
            WHERE sale_id=:id AND direction='RECEIPT'""",
            {"id": sale["id"]},
        )
        refunded = one(
            repo,
            """SELECT coalesce(sum(amount),0) AS amount
            FROM med.retail_return_refund WHERE receipt_id=:id""",
            {"id": receipt["id"]},
        )["amount"]
        if refunded + doc["gross_amount"] > receipt["amount"]:
            raise BusinessError("REFUND_EXCEEDED", "退款超过原零售单净收现金", 409)
        service.period(repo, ctx, entity, service.today(repo))
        fid = uuid4()
        number = document_number("RRF", fid)
        repo.execute(
            """INSERT INTO med.retail_return_refund(tenant_id,id,entity_id,
            return_id,sale_id,receipt_id,account_id,amount,number,note,created_by)
            VALUES(:t,:id,:e,:r,:sale,:receipt,:account,:amount,:n,:note,:a)""",
            {
                "t": ctx.tenant_id,
                "id": fid,
                "e": entity,
                "r": rid,
                "sale": sale["id"],
                "receipt": receipt["id"],
                "account": receipt["account_id"],
                "amount": doc["gross_amount"],
                "n": number,
                "note": body.reason,
                "a": ctx.principal_id,
            },
        )
        repo.execute("UPDATE med.return_document SET version=version+1 WHERE id=:id", {"id": rid})
        result = {
            "id": str(fid),
            "number": number,
            "return_id": str(rid),
            "sale_id": str(sale["id"]),
            "amount": format(doc["gross_amount"], ".2f"),
            "version": str(doc["version"] + 1),
            "remaining_amount": "0.00",
        }
        audit(
            repo,
            ctx,
            "retail_return_refund",
            fid,
            "finance.retail_return_refunded",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "retail_return_refund",
            fid,
            1,
            "finance.retail_return_refunded",
            result,
        )
        return result

    return service.permanent(
        ctx,
        "retail.return_refund",
        str(rid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )
