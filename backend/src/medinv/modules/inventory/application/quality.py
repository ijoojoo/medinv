import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.quality import DispositionCreate, DispositionReview
from medinv.modules.inventory.application.counts import BALANCES
from medinv.modules.inventory.domain.decimal_values import quantity
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, encode, one, version
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

DOCUMENTS = """SELECT d.*,i.qty,i.source_quality,i.lot_no,i.balance_id,
  i.product_snapshot->>'name' AS name,i.package_snapshot->>'base_unit' AS base_unit,
  w.name AS warehouse_name,l.code AS location_code
  FROM med.quality_disposition d JOIN med.quality_disposition_item i
    ON (i.tenant_id,i.disposition_id)=(d.tenant_id,d.id)
  JOIN med.stock_balance b ON (b.tenant_id,b.id)=(i.tenant_id,i.balance_id)
  JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
  JOIN med.location l ON (l.tenant_id,l.id)=(b.tenant_id,b.location_id)"""


class QualityService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    def candidates(
        self, ctx: TenantContext, entity: UUID, search: str, offset: int
    ) -> dict[str, Any]:
        ctx.require("quality.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT q.*,w.name AS warehouse_name,
                on_hand-reserved-blocked AS available,count(*) OVER() AS total
                FROM ({BALANCES}) q JOIN med.warehouse w ON (w.tenant_id,w.id)=
                  (q.tenant_id,q.warehouse_id)
                WHERE q.entity_id=:e AND q.owner_id=:e AND w.kind='PHYSICAL'
                AND w.archived_at IS NULL
                  AND quality_status IN ('QUARANTINE','UNQUALIFIED')
                  AND on_hand>reserved+blocked AND NOT serial_required
                  AND restricted_category IS NULL AND pool_qty>0
                  AND (q.name ILIKE :search OR sku_code ILIKE :search OR lot_no ILIKE :search)
                ORDER BY sku_code,lot_no,location_code,q.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "search": "%" + search + "%", "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def scope(self, ctx: TenantContext, did: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            entity = one(
                SqlSession(session),
                "SELECT entity_id FROM med.quality_disposition WHERE id=:id",
                {"id": did},
            )["entity_id"]
        ctx.require(permission, entity)
        return UUID(str(entity))

    def listing(
        self,
        ctx: TenantContext,
        entity: UUID,
        offset: int,
        *,
        search: str = "",
        state: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        kind: str = "",
    ) -> dict[str, Any]:
        ctx.require("quality.read", entity)
        filters, params = document_filters(
            "q",
            "concat_ws(' ',q.number,q.name,q.lot_no,q.warehouse_name,q.location_code,q.reason)",
            {
                "DRAFT": "q.state='DRAFT'",
                "APPROVED": "q.state='APPROVED'",
                "POSTED": "q.state='POSTED'",
                "CANCELLED": "q.state='CANCELLED'",
            },
            search,
            state,
            date_from,
            date_to,
        )
        if kind:
            if kind not in {"RELEASE", "WRITE_OFF"}:
                raise BusinessError("INVALID_QUERY", "处置方式无效", 422)
            filters += " AND q.kind=:disposition_kind"
            params["disposition_kind"] = kind
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"SELECT q.*,count(*) OVER() AS total FROM ({DOCUMENTS}) q "
                f"WHERE entity_id=:e {filters} "
                "ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def item(self, repo: SqlSession, did: UUID) -> dict[str, Any]:
        return one(
            repo,
            """SELECT i.*,b.sku_id,b.lot_id,1 AS line_no
          FROM med.quality_disposition_item i JOIN med.stock_balance b
          ON (b.tenant_id,b.id)=(i.tenant_id,i.balance_id) WHERE i.disposition_id=:id""",
            {"id": did},
        )

    def detail(self, ctx: TenantContext, did: UUID) -> dict[str, Any]:
        self.scope(ctx, did, "quality.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            doc = one(repo, DOCUMENTS + " WHERE d.id=:id", {"id": did})
            doc["version"] = str(doc["version"])
            result = None
            if doc["posting_id"]:
                result = one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["posting_id"]},
                )["result_snapshot"]
            return dict(encode({"document": doc, "item": self.item(repo, did), "result": result}))

    def source(self, repo: SqlSession, entity: UUID, bid: UUID, kind: str) -> dict[str, Any]:
        row = one(
            repo,
            BALANCES + " WHERE b.id=:id AND b.entity_id=:e AND b.owner_id=:e",
            {"id": bid, "e": entity},
        )
        warehouse = one(
            repo,
            "SELECT kind FROM med.warehouse WHERE id=:id AND entity_id=:e "
            "AND archived_at IS NULL FOR SHARE",
            {"id": row["warehouse_id"], "e": entity},
        )
        if (
            warehouse["kind"] != "PHYSICAL"
            or row["quality_status"] not in ("QUARANTINE", "UNQUALIFIED")
            or row["serial_required"]
            or row["restricted_category"] is not None
        ):
            raise BusinessError(
                "QUALITY_SOURCE_INVALID", "仅支持实体仓内自有普通隔离或不合格批次", 409
            )
        if kind == "RELEASE":
            now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
            if (
                row["quality_status"] != "QUARANTINE"
                or row["gate_blocked"]
                or row["quality_blocked"]
                or not row["expiry_exclusive"]
                or row["expiry_exclusive"] <= now
                or row["approval_state"] != "APPROVED"
                or not row["qualification_current"]
            ):
                raise BusinessError(
                    "QUALITY_RELEASE_INELIGIBLE",
                    "仅资质和效期有效、未冻结的隔离批次可放行；不合格批次不能直接转合格",
                    409,
                )
        return row

    @staticmethod
    def check_qty(row: dict[str, Any], qty: Decimal) -> None:
        if qty > row["on_hand"] - row["reserved"] - row["blocked"]:
            raise BusinessError("INSUFFICIENT_STOCK", "处置数量超过当前未占用库存", 409)
        if qty > row["pool_qty"]:
            raise BusinessError("QUALITY_COST_INVALID", "自有库存成本数量不足，不能处置", 409)

    def create(self, ctx: TenantContext, body: DispositionCreate, key: str) -> dict[str, Any]:
        ctx.require("quality.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "quality.write")
            row = self.source(repo, body.entity_id, body.balance_id, body.kind)
            if row["lot_no"] != body.lot_no:
                raise BusinessError("QUALITY_LOT_MISMATCH", "实物批号与选择批次不一致", 409)
            try:
                qty = quantity(body.qty, sku_scale=row["qty_scale"])
            except ValueError as exc:
                raise BusinessError("QTY_SCALE_INVALID", "数量不符合商品精度", 422) from exc
            self.check_qty(row, qty)
            did = uuid4()
            number = document_number("QD", did)
            repo.execute(
                """INSERT INTO med.quality_disposition
              (tenant_id,id,entity_id,number,kind,reason,evidence,created_by)
              VALUES(:t,:id,:e,:n,:kind,:reason,:evidence,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": did,
                    "e": body.entity_id,
                    "n": number,
                    "kind": body.kind,
                    "reason": body.reason,
                    "evidence": body.evidence,
                    "a": ctx.principal_id,
                },
            )
            repo.execute(
                """INSERT INTO med.quality_disposition_item
              (tenant_id,id,disposition_id,balance_id,qty,source_quality,lot_no,
              product_snapshot,package_snapshot,created_by)
              VALUES(:t,:id,:d,:b,:q,:quality,:lot,CAST(:product AS jsonb),
                CAST(:package AS jsonb),:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "d": did,
                    "b": body.balance_id,
                    "q": qty,
                    "quality": row["quality_status"],
                    "lot": body.lot_no,
                    "a": ctx.principal_id,
                    "product": json.dumps(
                        {
                            k: row[k]
                            for k in (
                                "name",
                                "sku_code",
                                "specification",
                                "manufacturer_name",
                                "approval_number",
                                "category",
                            )
                        }
                    ),
                    "package": json.dumps(
                        {
                            "base_unit": row["base_unit"],
                            "qty_scale": row["qty_scale"],
                            "numerator": 1,
                            "denominator": 1,
                        }
                    ),
                },
            )
            audit(
                repo,
                ctx,
                "quality_disposition",
                did,
                "inventory.quality_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(did), "number": number, "state": "DRAFT", "version": "1"}

        return self.finance.permanent(
            ctx,
            "quality.create",
            str(body.balance_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def transition(
        self, ctx: TenantContext, did: UUID, body: Transition, key: str, review: bool = False
    ) -> dict[str, Any]:
        permission = "quality.review" if review else "quality.write"
        entity = self.scope(ctx, did, permission)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, permission)
            doc = one(
                repo, "SELECT * FROM med.quality_disposition WHERE id=:id FOR UPDATE", {"id": did}
            )
            version(doc, body.expected_version)
            if doc["state"] not in (("DRAFT",) if review else ("DRAFT", "APPROVED")):
                raise BusinessError("INVALID_STATE", "仅未过账处置单可以审核或取消", 409)
            state = "CANCELLED"
            if review:
                assert isinstance(body, DispositionReview)
                if doc["created_by"] == ctx.principal_id:
                    raise BusinessError("MAKER_CHECKER", "处置单必须由其他人员独立审核", 403)
                state = "APPROVED" if body.decision == "APPROVE" else "CANCELLED"
                repo.execute(
                    """UPDATE med.quality_disposition SET reviewed_by=:a,
                  reviewed_at=clock_timestamp(),review_note=:note WHERE id=:id""",
                    {"id": did, "a": ctx.principal_id, "note": body.reason},
                )
            repo.execute(
                """UPDATE med.quality_disposition SET state=:state,version=version+1,
              cancel_note=CASE WHEN CAST(:state AS varchar)='CANCELLED' THEN :note ELSE NULL END
              WHERE id=:id""",
                {"id": did, "state": state, "note": body.reason},
            )
            result = {"id": str(did), "state": state, "version": str(doc["version"] + 1)}
            audit(
                repo,
                ctx,
                "quality_disposition",
                did,
                "inventory.quality_reviewed" if review else "inventory.quality_cancelled",
                {**result, "reason": body.reason},
            )
            return result

        return command(
            self.uow,
            ctx,
            "quality.review" if review else "quality.cancel",
            str(did),
            key,
            body.model_dump(mode="json"),
            run,
        )
