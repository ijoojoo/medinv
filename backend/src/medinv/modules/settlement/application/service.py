import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import (
    ZONE,
    document_number,
    encode,
    one,
    version,
)
from medinv.modules.settlement.api.schemas import AccountCreate, CashCreate
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SettlementService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    @staticmethod
    def today(repo: SqlSession) -> date:
        return (
            cast(datetime, one(repo, "SELECT clock_timestamp() AS now", {})["now"])
            .astimezone(ZONE)
            .date()
        )

    def entity(self, repo: SqlSession, ctx: TenantContext, entity: UUID, permission: str) -> None:
        ctx.require(permission, entity)
        row = one(repo, "SELECT status FROM med.legal_entity WHERE id=:e FOR UPDATE", {"e": entity})
        if row["status"] != "ACTIVE":
            raise BusinessError("ENTITY_UNAVAILABLE", "经营主体已停用", 409)

    def period(self, repo: SqlSession, ctx: TenantContext, entity: UUID, day: date) -> None:
        period = day.strftime("%Y-%m")
        repo.execute(
            """INSERT INTO med.accounting_period(tenant_id,entity_id,period)
          VALUES(:t,:e,:p) ON CONFLICT DO NOTHING""",
            {"t": ctx.tenant_id, "e": entity, "p": period},
        )
        row = one(
            repo,
            """SELECT state FROM med.accounting_period
          WHERE entity_id=:e AND period=:p FOR SHARE""",
            {"e": entity, "p": period},
        )
        if row["state"] != "OPEN":
            raise BusinessError("PERIOD_CLOSED", "记账期间已关闭", 409)

    def scope(self, ctx: TenantContext, cash_id: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id FROM med.cash_document WHERE id=:id",
                {"id": cash_id},
            )
        entity = UUID(str(row["entity_id"]))
        ctx.require(permission, entity)
        return entity

    def permanent(
        self,
        ctx: TenantContext,
        name: str,
        target: str,
        key: str,
        op: UUID,
        body: dict[str, Any],
        handler: Callable[[SqlSession], dict[str, Any]],
    ) -> dict[str, Any]:
        body_hash = fingerprint({"target": target, **body})

        def run(repo: SqlSession) -> dict[str, Any]:
            prior = repo.rows("SELECT * FROM med.settlement_operation WHERE id=:id", {"id": op})
            if prior:
                row = prior[0]
                if (
                    row["created_by"] != ctx.principal_id
                    or row["kind"] != name
                    or row["request_hash"] != body_hash
                ):
                    raise BusinessError("OPERATION_CONFLICT", "操作编号已被另一请求使用", 409)
                return dict(row["response"])
            response = handler(repo)
            repo.execute(
                """INSERT INTO med.settlement_operation
              (tenant_id,id,created_by,kind,request_hash,response)
              VALUES(:t,:id,:a,:kind,:hash,CAST(:result AS jsonb))""",
                {
                    "t": ctx.tenant_id,
                    "id": op,
                    "a": ctx.principal_id,
                    "kind": name,
                    "hash": body_hash,
                    "result": json.dumps(response),
                },
            )
            return response

        return command(self.uow, ctx, name, target, key, body, run, op)

    def accounts(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                "SELECT * FROM med.cash_account WHERE entity_id=:e ORDER BY code,id LIMIT 201",
                {"e": entity},
            )
            if len(rows) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "账户超过当前选项上限", 422)
            return {"items": encode(rows)}

    def create_account(self, ctx: TenantContext, body: AccountCreate, key: str) -> dict[str, Any]:
        ctx.require("finance.manage", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, body.entity_id, "finance.manage")
            if repo.rows(
                "SELECT id FROM med.cash_account WHERE entity_id=:e AND code=:c",
                {"e": body.entity_id, "c": body.code},
            ):
                raise BusinessError("ACCOUNT_CODE_EXISTS", "账户编码已存在", 409)
            aid = uuid4()
            repo.execute(
                """INSERT INTO med.cash_account(tenant_id,id,entity_id,code,name,method,created_by)
              VALUES(:t,:id,:entity_id,:code,:name,:method,:a)""",
                {
                    **body.model_dump(),
                    "t": ctx.tenant_id,
                    "id": aid,
                    "a": ctx.principal_id,
                },
            )
            audit(
                repo,
                ctx,
                "cash_account",
                aid,
                "finance.account_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(aid)}

        return command(
            self.uow,
            ctx,
            "finance.account",
            str(body.entity_id),
            key,
            body.model_dump(mode="json"),
            run,
        )

    def create_cash(self, ctx: TenantContext, body: CashCreate, key: str) -> dict[str, Any]:
        ctx.require("finance.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, body.entity_id, "finance.write")
            if body.occurred_on > self.today(repo):
                raise BusinessError("FUTURE_CASH_DATE", "不能确认未来发生的收付款", 422)
            self.period(repo, ctx, body.entity_id, body.occurred_on)
            one(
                repo,
                "SELECT id FROM med.party WHERE id=:id AND entity_id=:e",
                {"id": body.party_id, "e": body.entity_id},
            )
            one(
                repo,
                "SELECT id FROM med.cash_account WHERE id=:id AND entity_id=:e",
                {"id": body.account_id, "e": body.entity_id},
            )
            if repo.rows(
                """SELECT id FROM med.cash_document WHERE entity_id=:e AND account_id=:account
              AND direction=:direction AND reference=:ref AND state='CONFIRMED'
              UNION ALL SELECT id FROM med.cash_refund WHERE entity_id=:e AND account_id=:account
              AND direction=:direction AND reference=:ref AND state='CONFIRMED'""",
                {
                    "e": body.entity_id,
                    "account": body.account_id,
                    "direction": body.direction,
                    "ref": body.reference,
                },
            ):
                raise BusinessError(
                    "CASH_REFERENCE_EXISTS", "该账户及方向的凭据已登记，请先核查原记录", 409
                )
            cid = uuid4()
            number = document_number("CR" if body.direction == "RECEIPT" else "CP", cid)
            repo.execute(
                """INSERT INTO med.cash_document(tenant_id,id,entity_id,party_id,account_id,
              direction,number,reference,note,amount,occurred_on,created_by)
             
              VALUES(:t,:id,:entity_id,:party_id,:account_id,:direction,:n,:reference,:note,:amount,:occurred_on,:a)""",
                {
                    **body.model_dump(),
                    "amount": Decimal(body.amount),
                    "t": ctx.tenant_id,
                    "id": cid,
                    "n": number,
                    "a": ctx.principal_id,
                },
            )
            result = {"id": str(cid), "number": number, "version": "1"}
            audit(
                repo,
                ctx,
                "cash_document",
                cid,
                "finance.cash_recorded",
                body.model_dump(mode="json"),
            )
            event(repo, ctx, uuid4(), "cash_document", cid, 1, "finance.cash_recorded", result)
            return result

        return self.permanent(
            ctx,
            "finance.cash",
            str(body.entity_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def items(
        self,
        ctx: TenantContext,
        entity: UUID,
        kind: str,
        offset: int,
        party: UUID | None = None,
        open_only: bool = False,
    ) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT i.*,p.name AS party_name,coalesce(sh.number,rc.number) AS source_number,
              count(*) OVER() AS total FROM med.settlement_item i JOIN med.party p
              ON (p.tenant_id,p.id)=(i.tenant_id,i.party_id)
              LEFT JOIN med.receivable_obligation ar ON (ar.tenant_id,ar.id)=(i.tenant_id,i.id)
              AND i.kind='AR'
              LEFT JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(ar.tenant_id,ar.shipment_id)
              LEFT JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(i.tenant_id,i.posting_id)
              AND i.kind='AP' AND pb.source_type='RECEIPT_POSTING'
              LEFT JOIN med.receipt_posting rp ON (rp.tenant_id,rp.id)=(pb.tenant_id,pb.source_id)
              LEFT JOIN med.inspection ins ON (ins.tenant_id,ins.id)=(rp.tenant_id,rp.inspection_id)
              LEFT JOIN med.receipt rc ON (rc.tenant_id,rc.id)=(ins.tenant_id,ins.receipt_id)
              WHERE i.entity_id=:e AND i.kind=:kind AND (CAST(:party AS uuid) IS NULL
              OR i.party_id=CAST(:party AS uuid))
              AND (NOT :open OR i.outstanding_amount>0)
              ORDER BY i.created_at DESC,i.id LIMIT 50 OFFSET :offset""",
                {
                    "e": entity,
                    "kind": kind,
                    "party": str(party) if party else None,
                    "open": open_only,
                    "offset": offset,
                },
            )
            return {
                "items": encode([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def cash_list(self, ctx: TenantContext, entity: UUID, offset: int) -> dict[str, Any]:
        ctx.require("finance.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT c.*,p.name AS party_name,a.name AS account_name,
              c.amount-c.allocated_amount-c.refunded_amount AS remaining_amount,count(*)
              OVER() AS total
              FROM med.cash_document c JOIN med.party p
              ON (p.tenant_id,p.id)=(c.tenant_id,c.party_id)
              JOIN med.cash_account a ON (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
              WHERE c.entity_id=:e ORDER BY c.created_at DESC,c.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            return {
                "items": encode([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(self, ctx: TenantContext, cid: UUID, offset: int = 0) -> dict[str, Any]:
        self.scope(ctx, cid, "finance.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            cash = one(
                repo,
                """SELECT c.*,p.name AS party_name,a.name AS account_name,
              c.amount-c.allocated_amount-c.refunded_amount AS remaining_amount FROM
              med.cash_document c
              JOIN med.party p ON (p.tenant_id,p.id)=(c.tenant_id,c.party_id)
              JOIN med.cash_account a ON (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
              WHERE c.id=:id""",
                {"id": cid},
            )
            cash["version"] = str(cash["version"])
            rows = repo.rows(
                """SELECT a.*,coalesce(a.receivable_id,a.payable_id) AS item_id,
              coalesce(sh.number,rc.number) AS source_number,
              r.id AS reversal_id,r.reason AS reversal_reason,r.created_at AS reversed_at,
              count(*) OVER() AS total
              FROM med.settlement_allocation a LEFT JOIN med.settlement_reversal r
              ON (r.tenant_id,r.allocation_id)=(a.tenant_id,a.id)
              LEFT JOIN med.receivable_obligation ar
              ON (ar.tenant_id,ar.id)=(a.tenant_id,a.receivable_id)
              LEFT JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(ar.tenant_id,ar.shipment_id)
              LEFT JOIN med.payable_obligation ap
              ON (ap.tenant_id,ap.id)=(a.tenant_id,a.payable_id)
              LEFT JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(ap.tenant_id,ap.posting_id)
              AND pb.source_type='RECEIPT_POSTING'
              LEFT JOIN med.receipt_posting rp ON (rp.tenant_id,rp.id)=(pb.tenant_id,pb.source_id)
              LEFT JOIN med.inspection ins ON (ins.tenant_id,ins.id)=(rp.tenant_id,rp.inspection_id)
              LEFT JOIN med.receipt rc ON (rc.tenant_id,rc.id)=(ins.tenant_id,ins.receipt_id)
              WHERE a.cash_document_id=:id ORDER BY a.created_at DESC,a.id
              LIMIT 50 OFFSET :offset""",
                {"id": cid, "offset": offset},
            )
            void = repo.rows("SELECT * FROM med.cash_void WHERE cash_document_id=:id", {"id": cid})
            return dict(
                encode(
                    {
                        "cash": cash,
                        "allocations": rows,
                        "void": void[0] if void else None,
                        "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
                    }
                )
            )

    def void(self, ctx: TenantContext, cid: UUID, body: Transition, key: str) -> dict[str, Any]:
        entity = self.scope(ctx, cid, "finance.reverse")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, entity, "finance.reverse")
            cash = one(repo, "SELECT * FROM med.cash_document WHERE id=:id FOR UPDATE", {"id": cid})
            if cash["state"] == "VOIDED":
                return {"id": str(cid), "version": str(cash["version"]), "state": "VOIDED"}
            version(cash, body.expected_version)
            if cash["allocated_amount"] or cash["refunded_amount"]:
                raise BusinessError("CASH_ALLOCATED", "请先处理该款项全部有效核销及退款登记", 409)
            self.period(repo, ctx, entity, self.today(repo))
            repo.execute(
                """INSERT INTO med.cash_void(tenant_id,id,cash_document_id,reason,created_by)
              VALUES(:t,:id,:cash,:reason,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "cash": cid,
                    "reason": body.reason,
                    "a": ctx.principal_id,
                },
            )
            repo.execute(
                "UPDATE med.cash_document SET state='VOIDED',version=version+1 WHERE id=:id",
                {"id": cid},
            )
            result = {"id": str(cid), "version": str(cash["version"] + 1), "state": "VOIDED"}
            audit(
                repo, ctx, "cash_document", cid, "finance.cash_voided", body.model_dump(mode="json")
            )
            event(
                repo,
                ctx,
                uuid4(),
                "cash_document",
                cid,
                cash["version"] + 1,
                "finance.cash_voided",
                result,
            )
            return result

        return command(
            self.uow, ctx, "finance.void", str(cid), key, body.model_dump(mode="json"), run
        )
