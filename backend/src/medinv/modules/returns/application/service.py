from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.returns.api.schemas import ReturnCreate, ReturnReview
from medinv.modules.returns.application import manifest
from medinv.modules.returns.application.permissions import application_permission
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

SOURCES = """
 SELECT o.*,coalesce(p.name,'零售顾客') AS party_name,pb.posted_at,
   coalesce(sh.number,rc.number,pos.number) AS source_number
 FROM (
 SELECT tenant_id,id AS obligation_id,entity_id,customer_id AS party_id,
   'SALES'::text AS kind,posting_id,gross_amount,net_amount,NULL::uuid AS retail_sale_id
 FROM med.receivable_obligation
 UNION ALL
 SELECT tenant_id,id,entity_id,supplier_id,'PURCHASE'::text,posting_id,gross_amount,net_amount,
   NULL::uuid FROM med.payable_obligation
 UNION ALL
 SELECT tenant_id,NULL::uuid,entity_id,NULL::uuid,'RETAIL'::text,posting_id,gross_amount,
   net_amount,id FROM med.retail_sale WHERE state='COMPLETED'
 ) o JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(o.tenant_id,o.posting_id)
 LEFT JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.party_id)
 LEFT JOIN med.retail_sale pos ON (pos.tenant_id,pos.id)=(o.tenant_id,o.retail_sale_id)
 LEFT JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(pb.tenant_id,pb.source_id)
   AND o.kind='SALES'
 LEFT JOIN med.receipt_posting rp ON (rp.tenant_id,rp.id)=(pb.tenant_id,pb.source_id)
   AND o.kind='PURCHASE'
 LEFT JOIN med.inspection ins ON (ins.tenant_id,ins.id)=(rp.tenant_id,rp.inspection_id)
 LEFT JOIN med.receipt rc ON (rc.tenant_id,rc.id)=(ins.tenant_id,ins.receipt_id)
"""


class ReturnService(SettlementService):
    def source(self, ctx: TenantContext, pid: UUID, permission: str) -> dict[str, Any]:
        if permission == "returns.write":
            application_permission(ctx)
        else:
            ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                f"SELECT * FROM ({SOURCES}) s WHERE posting_id=:id",
                {"id": pid},
            )
        if permission == "returns.write":
            permission = application_permission(ctx, row["kind"])
        ctx.require(permission, row["entity_id"])
        return row

    def sources(self, ctx: TenantContext, entity: UUID, kind: str, offset: int) -> dict[str, Any]:
        ctx.require("returns.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""
              SELECT s.*,r.number AS return_number,r.state AS return_state,
                coalesce(rt.returned_amount,0) AS returned_amount,count(*) OVER() AS total
              FROM ({SOURCES}) s LEFT JOIN med.return_document r
                ON (r.tenant_id,r.source_posting_id)=(s.tenant_id,s.posting_id)
                AND r.state NOT IN ('POSTED','CANCELLED')
              LEFT JOIN (SELECT tenant_id,source_posting_id,sum(gross_amount) AS returned_amount
                FROM med.return_document WHERE state='POSTED'
                GROUP BY tenant_id,source_posting_id) rt
                ON (rt.tenant_id,rt.source_posting_id)=(s.tenant_id,s.posting_id)
              WHERE s.entity_id=:e AND s.kind=:kind ORDER BY s.posted_at DESC,s.posting_id
              LIMIT 50 OFFSET :offset""",
                {"e": entity, "kind": kind, "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def source_detail(self, ctx: TenantContext, pid: UUID, offset: int = 0) -> dict[str, Any]:
        source = self.source(ctx, pid, "returns.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            lines = manifest.remaining(repo, pid, self.lines(repo, pid))
            source.update(
                one(
                    repo,
                    """SELECT coalesce(sum(gross_amount) FILTER (WHERE state='POSTED'),0)
                        AS returned_amount FROM med.return_document WHERE source_posting_id=:p""",
                    {"p": pid},
                )
            )
            active = repo.rows(
                """SELECT id,number,state FROM med.return_document
                WHERE source_posting_id=:p AND state NOT IN ('POSTED','CANCELLED')""",
                {"p": pid},
            )
            history = repo.rows(
                """SELECT id,number,kind,state,gross_amount,reason,created_at
                FROM med.return_document WHERE source_posting_id=:p
                ORDER BY created_at DESC,id LIMIT 51 OFFSET :offset""",
                {"p": pid, "offset": offset},
            )
            return dict(
                encode(
                    {
                        "source": source,
                        "lines": lines,
                        "partial_supported": all(r["gross_amount"] is not None for r in lines),
                        "active_return": active[0] if active else None,
                        "history": {"items": history[:50], "has_more": len(history) > 50},
                    }
                )
            )

    def selected_lines(self, repo: SqlSession, doc: dict[str, Any]) -> list[dict[str, Any]]:
        return manifest.selected(repo, doc, self.lines(repo, doc["source_posting_id"]))

    def lines(self, repo: SqlSession, pid: UUID) -> list[dict[str, Any]]:
        rows = repo.rows(
            """
          SELECT pl.*,sl.id AS stock_entry_id,sl.balance_id,sl.qty_delta,
            cl.id AS cost_entry_id,cl.cost_balance_id,cl.amount_delta,
            l.lot_no,b.warehouse_id,b.location_id,b.lot_id,b.quality_status,
            pl.product_snapshot->>'name' AS name,pl.package_snapshot->>'base_unit' AS base_unit
          FROM med.posting_line pl JOIN med.stock_ledger sl
            ON (sl.tenant_id,sl.posting_line_id,sl.effect_index)=(pl.tenant_id,pl.id,0)
          JOIN med.cost_ledger cl
            ON (cl.tenant_id,cl.posting_line_id,cl.effect_index)=(pl.tenant_id,pl.id,0)
          JOIN med.stock_balance b ON (b.tenant_id,b.id)=(sl.tenant_id,sl.balance_id)
          JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
          WHERE pl.posting_id=:p ORDER BY sl.balance_id,pl.line_no LIMIT 501""",
            {"p": pid},
        )
        if not rows or len(rows) > 500:
            raise BusinessError(
                "RETURN_SOURCE_UNSUPPORTED", "来源必须有 1–500 条普通批次过账明细", 422
            )
        return rows

    def create(self, ctx: TenantContext, body: ReturnCreate, key: str) -> dict[str, Any]:
        source = self.source(ctx, body.source_posting_id, "returns.write")
        permission = application_permission(ctx, source["kind"])

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, source["entity_id"], permission)
            if repo.rows(
                """SELECT id FROM med.return_document
              WHERE source_posting_id=:p AND state NOT IN ('POSTED','CANCELLED')""",
                {"p": body.source_posting_id},
            ):
                raise BusinessError("RETURN_EXISTS", "原批次已有待处理退货，请先完成或取消", 409)
            lines = manifest.remaining(
                repo, body.source_posting_id, self.lines(repo, body.source_posting_id)
            )
            selected, gross, net = manifest.prepare(source, lines, body)
            rid = uuid4()
            number = document_number(
                {"SALES": "SR", "PURCHASE": "PR", "RETAIL": "RR"}[source["kind"]], rid
            )
            repo.execute(
                """
              INSERT INTO med.return_document(tenant_id,id,entity_id,party_id,source_posting_id,
                receivable_id,payable_id,kind,number,reason,gross_amount,net_amount,created_by,
                workflow_version,retail_sale_id)
              VALUES(:t,:id,:e,:party,:p,:ar,:ap,:kind,:n,:reason,:gross,:net,:a,2,:retail)""",
                {
                    "t": ctx.tenant_id,
                    "id": rid,
                    "e": source["entity_id"],
                    "party": source["party_id"],
                    "retail": source["retail_sale_id"],
                    "p": body.source_posting_id,
                    "ar": source["obligation_id"] if source["kind"] == "SALES" else None,
                    "ap": source["obligation_id"] if source["kind"] == "PURCHASE" else None,
                    "kind": source["kind"],
                    "n": number,
                    "reason": body.reason,
                    "gross": gross,
                    "net": net,
                    "a": ctx.principal_id,
                },
            )
            for line in selected:
                repo.execute(
                    """INSERT INTO med.return_line(tenant_id,id,return_id,
                    source_posting_line_id,qty,gross_amount,net_amount,original_cost,created_by)
                    VALUES(:t,:id,:r,:source,:qty,:gross,:net,:cost,:a)""",
                    {
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "r": rid,
                        "source": line["id"],
                        "qty": line["base_qty"],
                        "gross": line["gross_amount"],
                        "net": line["net_amount"],
                        "cost": line["original_cost"],
                        "a": ctx.principal_id,
                    },
                )
            audit(repo, ctx, "return", rid, "returns.created", body.model_dump(mode="json"))
            return {"id": str(rid), "number": number, "version": "1"}

        return self.permanent(
            ctx,
            "returns.create",
            str(body.source_posting_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def return_scope(self, ctx: TenantContext, rid: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id FROM med.return_document WHERE id=:id",
                {"id": rid},
            )
        ctx.require(permission, row["entity_id"])
        return UUID(str(row["entity_id"]))

    def application_scope(self, ctx: TenantContext, rid: UUID) -> tuple[UUID, str]:
        application_permission(ctx)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session),
                "SELECT entity_id,kind FROM med.return_document WHERE id=:id",
                {"id": rid},
            )
        permission = application_permission(ctx, row["kind"])
        ctx.require(permission, row["entity_id"])
        return UUID(str(row["entity_id"])), permission

    def listing(
        self, ctx: TenantContext, entity: UUID, offset: int, custody_only: bool = False
    ) -> dict[str, Any]:
        ctx.require("returns.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT r.*,coalesce(p.name,'零售顾客') AS party_name,
                c.zone_label,c.created_at AS received_at,
                count(*) OVER() AS total
              FROM med.return_document r LEFT JOIN med.party p ON
              (p.tenant_id,p.id)=(r.tenant_id,r.party_id)
              LEFT JOIN med.return_custody c ON (c.tenant_id,c.return_id)=(r.tenant_id,r.id)
              LEFT JOIN med.return_custody_close cl
                ON (cl.tenant_id,cl.return_id)=(r.tenant_id,r.id)
              WHERE r.entity_id=:e AND (NOT :custody OR (c.id IS NOT NULL AND cl.id IS NULL))
              ORDER BY r.created_at DESC,r.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, "custody": custody_only},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(self, ctx: TenantContext, cid: UUID, offset: int = 0) -> dict[str, Any]:
        self.return_scope(ctx, cid, "returns.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = one(
                repo,
                f"""SELECT r.*,s.party_name,s.source_number,s.posted_at AS source_posted_at
              FROM med.return_document r JOIN ({SOURCES}) s
                ON (s.tenant_id,s.posting_id)=(r.tenant_id,r.source_posting_id)
              WHERE r.id=:id""",
                {"id": cid},
            )
            row["version"] = str(row["version"])
            credits = repo.rows(
                """SELECT a.id,a.cash_document_id,c.number AS cash_number,a.amount,
                count(*) OVER() AS total
              FROM med.settlement_allocation a JOIN med.cash_document c
                ON (c.tenant_id,c.id)=(a.tenant_id,a.cash_document_id)
              LEFT JOIN med.settlement_reversal rev ON
              (rev.tenant_id,rev.allocation_id)=(a.tenant_id,a.id)
              WHERE coalesce(a.receivable_id,a.payable_id)=:o AND rev.id IS NULL
              ORDER BY a.created_at,a.id LIMIT 50 OFFSET :offset""",
                {"o": row["receivable_id"] or row["payable_id"], "offset": offset},
            )
            result = None
            if row["posting_id"]:
                result = one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": row["posting_id"]},
                )["result_snapshot"]
            return dict(
                encode(
                    {
                        "document": row,
                        "lines": self.selected_lines(repo, row),
                        "custody": repo.rows(
                            """SELECT c.*,cl.outcome,cl.note AS close_note,
                            cl.created_at AS closed_at FROM med.return_custody c
                            LEFT JOIN med.return_custody_close cl
                              ON (cl.tenant_id,cl.return_id)=(c.tenant_id,c.return_id)
                            WHERE c.return_id=:id""",
                            {"id": cid},
                        ),
                        "allocations": credits,
                        "allocations_has_more": bool(
                            credits and offset + len(credits) < credits[0]["total"]
                        ),
                        "result": result,
                    }
                )
            )

    def review(self, ctx: TenantContext, rid: UUID, body: ReturnReview, key: str) -> dict[str, Any]:
        entity = self.return_scope(ctx, rid, "returns.review")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, entity, "returns.review")
            row = one(
                repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid}
            )
            version(row, body.expected_version)
            if row["created_by"] == ctx.principal_id:
                raise BusinessError("MAKER_CHECKER", "退货必须由独立成员复核", 403)
            custody_required = row["kind"] in {"SALES", "RETAIL"} and row["workflow_version"] == 2
            if row["state"] != ("RECEIVED" if custody_required else "DRAFT"):
                raise BusinessError("INVALID_STATE", "销售退货须先隔离收货，再独立复核", 409)
            if custody_required:
                receipt = one(
                    repo, "SELECT * FROM med.return_custody WHERE return_id=:id", {"id": rid}
                )
                if receipt["created_by"] == ctx.principal_id:
                    raise BusinessError("MAKER_CHECKER", "收货人不能复核自己的隔离收货", 403)
            expected = {
                str(r["id"]): (r["base_qty"], r["lot_no"]) for r in self.selected_lines(repo, row)
            }
            actual = {str(r.source_posting_line_id): (Decimal(r.qty), r.lot_no) for r in body.lines}
            if body.decision == "APPROVE" and actual != expected:
                raise BusinessError(
                    "RETURN_CHECK_MISMATCH", "实物数量、批号必须逐行与本次退货明细一致", 409
                )
            state = (
                "APPROVED"
                if body.decision == "APPROVE"
                else "REJECTED"
                if custody_required
                else "CANCELLED"
            )
            repo.execute(
                """UPDATE med.return_document SET state=:s,reviewed_by=:a,
              review_note=:note,reviewed_at=clock_timestamp(),version=version+1 WHERE id=:id""",
                {"s": state, "a": ctx.principal_id, "note": body.reason, "id": rid},
            )
            audit(repo, ctx, "return", rid, "returns.reviewed", body.model_dump(mode="json"))
            return {"id": str(rid), "state": state, "version": str(row["version"] + 1)}

        return command(
            self.uow, ctx, "returns.review", str(rid), key, body.model_dump(mode="json"), run
        )

    def cancel(self, ctx: TenantContext, rid: UUID, body: Transition, key: str) -> dict[str, Any]:
        entity, permission = self.application_scope(ctx, rid)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.entity(repo, ctx, entity, permission)
            row = one(
                repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid}
            )
            version(row, body.expected_version)
            if row["state"] not in ("DRAFT", "APPROVED"):
                raise BusinessError("INVALID_STATE", "已过账或已取消退货不能取消", 409)
            if repo.rows("SELECT id FROM med.return_custody WHERE return_id=:id", {"id": rid}):
                raise BusinessError("CUSTODY_HAND_BACK_REQUIRED", "实物已收取，请登记交还客户", 409)
            repo.execute(
                "UPDATE med.return_document SET state='CANCELLED',version=version+1 WHERE id=:id",
                {"id": rid},
            )
            audit(repo, ctx, "return", rid, "returns.cancelled", body.model_dump(mode="json"))
            return {"id": str(rid), "state": "CANCELLED", "version": str(row["version"] + 1)}

        return command(
            self.uow, ctx, "returns.cancel", str(rid), key, body.model_dump(mode="json"), run
        )
