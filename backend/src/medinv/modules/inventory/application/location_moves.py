import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.location_moves import LocationMoveCreate, LocationMoveReview
from medinv.modules.inventory.application.counts import BALANCES
from medinv.modules.inventory.application.transfers import TransferService
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

DOCUMENTS = """SELECT d.*,i.qty,i.lot_no,i.balance_id,
  i.product_snapshot->>'name' AS name,i.package_snapshot->>'base_unit' AS base_unit,
  w.name AS warehouse_name,l.code AS source_location,coalesce(l.name,l.code) AS source_name,
  dest.code AS destination_location,coalesce(dest.name,dest.code) AS destination_name
  FROM med.location_move d JOIN med.location_move_item i
    ON (i.tenant_id,i.move_id)=(d.tenant_id,d.id)
  JOIN med.stock_balance b ON (b.tenant_id,b.id)=(i.tenant_id,i.balance_id)
  JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
  JOIN med.location l ON (l.tenant_id,l.id)=(b.tenant_id,b.location_id)
  JOIN med.location dest ON (dest.tenant_id,dest.id)=(d.tenant_id,d.destination_location_id)"""


class LocationMoveService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    def candidates(
        self, ctx: TenantContext, entity: UUID, search: str, offset: int
    ) -> dict[str, Any]:
        ctx.require("transfers.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT q.*,w.name AS warehouse_name,
                on_hand-reserved-blocked AS available,count(*) OVER() AS total
                FROM ({BALANCES}) q JOIN med.warehouse w ON (w.tenant_id,w.id)=
                  (q.tenant_id,q.warehouse_id)
                WHERE q.entity_id=:e AND q.owner_id=:e AND w.kind='PHYSICAL'
                AND w.archived_at IS NULL
                  AND quality_status='QUALIFIED'
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
                "SELECT entity_id FROM med.location_move WHERE id=:id",
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
    ) -> dict[str, Any]:
        ctx.require("transfers.read", entity)
        filters, params = document_filters(
            "q",
            "concat_ws(' ',q.number,q.name,q.lot_no,q.warehouse_name,"
            "q.source_location,q.destination_location,q.reason)",
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
          FROM med.location_move_item i JOIN med.stock_balance b
          ON (b.tenant_id,b.id)=(i.tenant_id,i.balance_id) WHERE i.move_id=:id""",
            {"id": did},
        )

    def detail(self, ctx: TenantContext, did: UUID) -> dict[str, Any]:
        self.scope(ctx, did, "transfers.read")
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

    def source(self, repo: SqlSession, entity: UUID, bid: UUID) -> dict[str, Any]:
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
        if warehouse["kind"] != "PHYSICAL":
            raise BusinessError("MOVE_WAREHOUSE", "仅支持实体仓内自有合格库存移库", 409)
        TransferService(self.uow).eligible(repo, row)
        return row

    @staticmethod
    def destination(repo: SqlSession, row: dict[str, Any], location: UUID) -> None:
        dest = one(
            repo, "SELECT warehouse_id FROM med.location WHERE id=:id FOR SHARE", {"id": location}
        )
        if dest["warehouse_id"] != row["warehouse_id"]:
            raise BusinessError("MOVE_CROSS_WAREHOUSE", "同仓移库不能跨仓，请使用仓库调拨", 409)
        if location == row["location_id"]:
            raise BusinessError("MOVE_SAME_LOCATION", "来源库位与目标库位必须不同", 422)

    @staticmethod
    def check_qty(row: dict[str, Any], qty: Decimal) -> None:
        if qty > row["on_hand"] - row["reserved"] - row["blocked"]:
            raise BusinessError("INSUFFICIENT_STOCK", "移库数量超过当前未占用库存", 409)

    def destinations(
        self, ctx: TenantContext, entity: UUID, bid: UUID, offset: int
    ) -> dict[str, Any]:
        ctx.require("transfers.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = one(
                repo,
                "SELECT warehouse_id,location_id FROM med.stock_balance "
                "WHERE id=:id AND entity_id=:e AND owner_id=:e",
                {"id": bid, "e": entity},
            )
            rows = repo.rows(
                """SELECT id,code,coalesce(name,code) AS name FROM med.location
                WHERE warehouse_id=:w AND id<>:l ORDER BY code,id LIMIT 201 OFFSET :offset""",
                {"w": row["warehouse_id"], "l": row["location_id"], "offset": offset},
            )
            return dict(
                encode(
                    {
                        "items": rows[:200],
                        "has_more": len(rows) > 200,
                        "next_offset": offset + 200 if len(rows) > 200 else None,
                    }
                )
            )

    def create(self, ctx: TenantContext, body: LocationMoveCreate, key: str) -> dict[str, Any]:
        ctx.require("transfers.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "transfers.write")
            row = self.source(repo, body.entity_id, body.balance_id)
            self.destination(repo, row, body.destination_location_id)
            if row["lot_no"] != body.lot_no:
                raise BusinessError("MOVE_LOT_MISMATCH", "实物批号与选择批次不一致", 409)
            try:
                qty = quantity(body.qty, sku_scale=row["qty_scale"])
            except ValueError as exc:
                raise BusinessError("QTY_SCALE_INVALID", "数量不符合商品精度", 422) from exc
            self.check_qty(row, qty)
            did = uuid4()
            number = document_number("LM", did)
            repo.execute(
                """INSERT INTO med.location_move
              (tenant_id,id,entity_id,number,destination_location_id,reason,created_by)
              VALUES(:t,:id,:e,:n,:dest,:reason,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": did,
                    "e": body.entity_id,
                    "n": number,
                    "dest": body.destination_location_id,
                    "reason": body.reason,
                    "a": ctx.principal_id,
                },
            )
            repo.execute(
                """INSERT INTO med.location_move_item
              (tenant_id,id,move_id,balance_id,qty,lot_no,
              product_snapshot,package_snapshot,created_by)
              VALUES(:t,:id,:d,:b,:q,:lot,CAST(:product AS jsonb),
                CAST(:package AS jsonb),:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "d": did,
                    "b": body.balance_id,
                    "q": qty,
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
                "location_move",
                did,
                "inventory.location_move_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(did), "number": number, "state": "DRAFT", "version": "1"}

        return self.finance.permanent(
            ctx,
            "location_move.create",
            str(body.balance_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def transition(
        self, ctx: TenantContext, did: UUID, body: Transition, key: str, review: bool = False
    ) -> dict[str, Any]:
        permission = "transfers.review" if review else "transfers.write"
        entity = self.scope(ctx, did, permission)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, permission)
            doc = one(repo, "SELECT * FROM med.location_move WHERE id=:id FOR UPDATE", {"id": did})
            version(doc, body.expected_version)
            if doc["state"] not in (("DRAFT",) if review else ("DRAFT", "APPROVED")):
                raise BusinessError("INVALID_STATE", "仅未过账移库单可以审核或取消", 409)
            state = "CANCELLED"
            if review:
                assert isinstance(body, LocationMoveReview)
                if doc["created_by"] == ctx.principal_id:
                    raise BusinessError("MAKER_CHECKER", "移库单必须由其他人员独立审核", 403)
                state = "APPROVED" if body.decision == "APPROVE" else "CANCELLED"
                repo.execute(
                    """UPDATE med.location_move SET reviewed_by=:a,
                  reviewed_at=clock_timestamp(),review_note=:note WHERE id=:id""",
                    {"id": did, "a": ctx.principal_id, "note": body.reason},
                )
            repo.execute(
                """UPDATE med.location_move SET state=:state,version=version+1,
              cancel_note=CASE WHEN CAST(:state AS varchar)='CANCELLED' THEN :note ELSE NULL END
              WHERE id=:id""",
                {"id": did, "state": state, "note": body.reason},
            )
            result = {"id": str(did), "state": state, "version": str(doc["version"] + 1)}
            audit(
                repo,
                ctx,
                "location_move",
                did,
                "inventory.location_move_reviewed"
                if review
                else "inventory.location_move_cancelled",
                {**result, "reason": body.reason},
            )
            return result

        return command(
            self.uow,
            ctx,
            "location_move.review" if review else "location_move.cancel",
            str(did),
            key,
            body.model_dump(mode="json"),
            run,
        )
