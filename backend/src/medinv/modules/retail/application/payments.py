from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.schemas import ReleaseRequest
from medinv.modules.procurement.application.service import one, version
from medinv.modules.retail.api.schemas import RetailCashConfirmation, RetailPayment
from medinv.modules.retail.application.service import RetailService
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def receive(
    service: RetailService, ctx: TenantContext, sid: UUID, body: RetailPayment, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, sid, "retail.sell")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "retail.sell")
        sale = one(repo, "SELECT * FROM med.retail_sale WHERE id=:id FOR UPDATE", {"id": sid})
        version(sale, body.expected_version)
        if sale["state"] != "RESERVED":
            raise BusinessError("RETAIL_ALREADY_PAID", "该单不能再次收款，请刷新核对状态", 409)
        reservation = one(
            repo,
            "SELECT * FROM med.reservation WHERE id=:id FOR UPDATE",
            {"id": sale["reservation_id"]},
        )
        now = service.sales.now(repo)
        if reservation["state"] != "ACTIVE" or reservation["expires_at"] <= now:
            raise BusinessError("RESERVATION_EXPIRED", "预占已失效，请先取消重建，勿继续收款", 409)
        one(
            repo,
            """SELECT id FROM med.cash_account WHERE id=:id AND entity_id=:e
            AND method='CASH'""",
            {"id": body.account_id, "e": entity},
        )
        tendered = Decimal(body.tendered)
        if tendered < sale["gross_amount"]:
            raise BusinessError("CASH_SHORT", "实收现金小于应收金额", 422)
        service.finance.period(repo, ctx, entity, service.finance.today(repo))
        pid = uuid4()
        change = tendered - sale["gross_amount"]
        repo.execute(
            """INSERT INTO med.retail_payment(tenant_id,id,sale_id,entity_id,account_id,
            direction,amount,tendered,change_amount,note,created_by)
            VALUES(:t,:id,:s,:e,:account,'RECEIPT',:amount,:tendered,:change,:note,:a)""",
            {
                "t": ctx.tenant_id,
                "id": pid,
                "s": sid,
                "e": entity,
                "account": body.account_id,
                "amount": sale["gross_amount"],
                "tendered": tendered,
                "change": change,
                "note": body.reason,
                "a": ctx.principal_id,
            },
        )
        repo.execute(
            """UPDATE med.reservation SET protected_until=TIMESTAMPTZ '9999-12-31 00:00:00+00',
            version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"id": sale["reservation_id"], "a": ctx.principal_id},
        )
        repo.execute(
            """UPDATE med.inventory_demand SET state='PAID_PROTECTED',version=version+1
            WHERE id=:id""",
            {"id": sid},
        )
        repo.execute(
            """UPDATE med.retail_sale SET state='PAID_PENDING_POST',version=version+1
            WHERE id=:id""",
            {"id": sid},
        )
        result = {
            "id": str(sid),
            "state": "PAID_PENDING_POST",
            "version": str(sale["version"] + 1),
            "payment_id": str(pid),
            "amount": format(sale["gross_amount"], ".2f"),
            "change_amount": format(change, ".2f"),
        }
        audit(
            repo,
            ctx,
            "retail_sale",
            sid,
            "finance.retail_cash_received",
            body.model_dump(mode="json"),
        )
        event(
            repo,
            ctx,
            uuid4(),
            "retail_sale",
            sid,
            sale["version"] + 1,
            "finance.retail_cash_received",
            result,
        )
        return result

    return service.finance.permanent(
        ctx,
        "retail.cash",
        str(sid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )


def refund_pending(
    service: RetailService, ctx: TenantContext, sid: UUID, body: RetailCashConfirmation, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, sid, "retail.refund")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "retail.refund")
        sale = one(repo, "SELECT * FROM med.retail_sale WHERE id=:id FOR UPDATE", {"id": sid})
        version(sale, body.expected_version)
        if sale["state"] != "PAID_PENDING_POST":
            raise BusinessError("RETAIL_REFUND_STATE", "仅已收款未出库零售单可以退单退款", 409)
        service.finance.period(repo, ctx, entity, service.finance.today(repo))
        receipt = one(
            repo,
            "SELECT * FROM med.retail_payment WHERE sale_id=:id AND direction='RECEIPT'",
            {"id": sid},
        )
        reservation = one(
            repo,
            "SELECT * FROM med.reservation WHERE id=:id FOR UPDATE",
            {"id": sale["reservation_id"]},
        )
        repo.execute(
            """INSERT INTO med.retail_payment(tenant_id,id,sale_id,entity_id,account_id,
            direction,amount,tendered,change_amount,note,created_by)
            VALUES(:t,:id,:s,:e,:account,'REFUND',:amount,:amount,0,:note,:a)""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "s": sid,
                "e": entity,
                "account": receipt["account_id"],
                "amount": receipt["amount"],
                "note": body.reason,
                "a": ctx.principal_id,
            },
        )
        repo.execute(
            "UPDATE med.reservation SET protected_until=NULL WHERE id=:id",
            {"id": sale["reservation_id"]},
        )
        repo.execute("UPDATE med.inventory_demand SET state='CANCELLED' WHERE id=:id", {"id": sid})
        service.inventory._release(
            repo,
            ctx,
            sale["reservation_id"],
            ReleaseRequest(expected_version=str(reservation["version"]), reason=body.reason),
            retail=True,
            permission="retail.refund",
        )
        repo.execute(
            "UPDATE med.retail_sale SET state='REFUNDED',version=version+1 WHERE id=:id",
            {"id": sid},
        )
        result = {
            "id": str(sid),
            "state": "REFUNDED",
            "version": str(sale["version"] + 1),
            "amount": format(receipt["amount"], ".2f"),
        }
        audit(
            repo,
            ctx,
            "retail_sale",
            sid,
            "finance.retail_cash_refunded",
            body.model_dump(mode="json"),
        )
        event(
            repo,
            ctx,
            uuid4(),
            "retail_sale",
            sid,
            sale["version"] + 1,
            "finance.retail_cash_refunded",
            result,
        )
        return result

    return service.finance.permanent(
        ctx,
        "retail.refund",
        str(sid),
        key,
        body.client_operation_id,
        body.model_dump(mode="json"),
        run,
    )
