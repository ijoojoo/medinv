import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.transfers import (
    TransferCreate,
    TransferReview,
    WarehouseCreate,
)
from medinv.modules.inventory.application.counts import BALANCES
from medinv.modules.inventory.application.warehouses import WarehouseService
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

DOCUMENTS = """SELECT d.*,s.name AS source_name,w.name AS destination_name,
  l.code AS destination_location FROM med.transfer d
  JOIN med.warehouse s ON (s.tenant_id,s.id)=(d.tenant_id,d.source_warehouse_id)
  JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.destination_warehouse_id)
  JOIN med.location l ON (l.tenant_id,l.id)=(d.tenant_id,d.destination_location_id)"""


class TransferService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    def options(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("transfers.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            warehouses = repo.rows(
                "SELECT id,name,code FROM med.warehouse WHERE entity_id=:e AND kind='PHYSICAL' "
                "AND archived_at IS NULL "
                "ORDER BY name,id LIMIT 201",
                {"e": entity},
            )
            locations = repo.rows(
                """SELECT l.id,l.code,coalesce(l.name,l.code) AS name,l.warehouse_id
                FROM med.location l JOIN med.warehouse w
                ON (w.tenant_id,w.id)=(l.tenant_id,l.warehouse_id)
                WHERE w.entity_id=:e AND w.kind='PHYSICAL'
                AND w.archived_at IS NULL ORDER BY l.code,l.id LIMIT 501""",
                {"e": entity},
            )
            if len(warehouses) > 200 or len(locations) > 500:
                raise BusinessError("RESULT_TOO_LARGE", "仓库或库位超出当前选项上限", 422)
            return dict(encode({"warehouses": warehouses, "locations": locations}))

    def create_warehouse(
        self, ctx: TenantContext, body: WarehouseCreate, key: str
    ) -> dict[str, Any]:
        return WarehouseService(self.uow).create(ctx, body, key)

    def candidates(
        self, ctx: TenantContext, entity: UUID, warehouse: UUID, search: str, offset: int
    ) -> dict[str, Any]:
        ctx.require("transfers.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT q.*,on_hand-reserved-blocked AS available,count(*) OVER() AS total
                FROM ({BALANCES}) q WHERE entity_id=:e AND warehouse_id=:w
                  AND quality_status='QUALIFIED' AND on_hand>reserved+blocked
                  AND NOT serial_required AND restricted_category IS NULL
                  AND EXISTS (SELECT 1 FROM med.warehouse w WHERE w.id=q.warehouse_id
                    AND w.kind='PHYSICAL' AND w.archived_at IS NULL)
                  AND (name ILIKE :search OR sku_code ILIKE :search OR lot_no ILIKE :search)
                ORDER BY sku_code,lot_no,location_code,id LIMIT 50 OFFSET :offset""",
                {"e": entity, "w": warehouse, "search": "%" + search + "%", "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def scope(self, ctx: TenantContext, tid: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            entity = one(
                SqlSession(session), "SELECT entity_id FROM med.transfer WHERE id=:id", {"id": tid}
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
            "concat_ws(' ',q.number,q.source_name,q.destination_name,q.reason)",
            {
                "DRAFT": "q.state='DRAFT'",
                "APPROVED": "q.state='APPROVED'",
                "IN_TRANSIT": "q.state='IN_TRANSIT'",
                "PART_RECEIVED": "q.state='PART_RECEIVED'",
                "RECEIVED": "q.state='RECEIVED'",
                "CLOSED_WITH_LOSS": "q.state='CLOSED_WITH_LOSS'",
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

    def lines(self, repo: SqlSession, tid: UUID) -> list[dict[str, Any]]:
        rows = repo.rows(
            """SELECT l.*,b.sku_id,b.lot_id,lot.lot_no,loc.code AS source_location,
          l.product_snapshot->>'name' AS name,l.package_snapshot->>'base_unit' AS base_unit,
          (l.package_snapshot->>'qty_scale')::int AS qty_scale,
          COALESCE((SELECT sum(r.qty) FROM med.transfer_receipt_line r
            WHERE (r.tenant_id,r.line_id)=(l.tenant_id,l.id)),0) AS received_qty,
          COALESCE((SELECT sum(x.qty) FROM med.transfer_loss x
            WHERE (x.tenant_id,x.line_id)=(l.tenant_id,l.id) AND x.state='POSTED'),0) AS lost_qty
          FROM med.transfer_line l JOIN med.stock_balance b
            ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id)
          JOIN med.lot lot ON (lot.tenant_id,lot.id)=(b.tenant_id,b.lot_id)
          JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
          WHERE l.transfer_id=:id ORDER BY l.line_no""",
            {"id": tid},
        )

        for row in rows:
            row["remaining_qty"] = row["qty"] - row["received_qty"] - row["lost_qty"]
        return rows

    @staticmethod
    def receipt_state(received: Decimal, lost: Decimal, total: Decimal) -> str:
        if received + lost == total:
            return "CLOSED_WITH_LOSS" if lost > 0 else "RECEIVED"
        return "PART_RECEIVED" if received > 0 else "IN_TRANSIT"

    def detail(self, ctx: TenantContext, tid: UUID, offset: int = 0) -> dict[str, Any]:
        self.scope(ctx, tid, "transfers.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            doc = one(repo, DOCUMENTS + " WHERE d.id=:id", {"id": tid})
            doc["version"] = str(doc["version"])
            receipts = repo.rows(
                """SELECT r.*,count(*) OVER() AS total,
              (SELECT jsonb_agg(jsonb_build_object('line_id',l.line_id,'qty',l.qty::text,
                'quality_status',l.quality_status) ORDER BY l.line_id)
               FROM med.transfer_receipt_line l
               WHERE (l.tenant_id,l.receipt_id)=(r.tenant_id,r.id)) AS lines
              FROM med.transfer_receipt r WHERE r.transfer_id=:id
              ORDER BY r.created_at DESC,r.id LIMIT 50 OFFSET :offset""",
                {"id": tid, "offset": offset},
            )
            return dict(
                encode(
                    {
                        "document": doc,
                        "lines": self.lines(repo, tid),
                        "receipts": receipts,
                        "has_more": bool(
                            receipts and offset + len(receipts) < receipts[0]["total"]
                        ),
                    }
                )
            )

    def warehouses(
        self, repo: SqlSession, entity: UUID, source: UUID, destination: UUID, location: UUID
    ) -> None:
        if source == destination:
            raise BusinessError("SAME_WAREHOUSE", "调出仓与调入仓必须不同", 422)
        rows = repo.rows(
            "SELECT * FROM med.warehouse WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
            {"ids": sorted([source, destination])},
        )
        if len(rows) != 2:
            raise BusinessError("NOT_FOUND", "仓库不可见或不存在", 404)
        if any(r["entity_id"] != entity for r in rows):
            raise BusinessError("CROSS_ENTITY_TRANSFER_FORBIDDEN", "仅支持同一经营主体内调拨", 409)
        if any(r["kind"] != "PHYSICAL" or r["archived_at"] for r in rows):
            raise BusinessError("TRANSFER_WAREHOUSE", "请选择实体仓库", 422)
        one(
            repo,
            "SELECT id FROM med.location WHERE id=:l AND warehouse_id=:w FOR SHARE",
            {"l": location, "w": destination},
        )

    def eligible(self, repo: SqlSession, row: dict[str, Any]) -> None:
        now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
        if (
            row["serial_required"]
            or row["restricted_category"] is not None
            or row["quality_status"] != "QUALIFIED"
            or row["gate_blocked"]
            or row["quality_blocked"]
            or not row["expiry_exclusive"]
            or row["expiry_exclusive"] <= now
            or row["approval_state"] != "APPROVED"
            or not row["qualification_current"]
        ):
            raise BusinessError(
                "TRANSFER_INELIGIBLE",
                "仅有效、未冻结的普通合格批次可调出或合格收货；异常到货请选隔离待验",
                409,
            )

    def create(self, ctx: TenantContext, body: TransferCreate, key: str) -> dict[str, Any]:
        ctx.require("transfers.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "transfers.write")
            self.warehouses(
                repo,
                body.entity_id,
                body.source_warehouse_id,
                body.destination_warehouse_id,
                body.destination_location_id,
            )
            rows = {
                r["id"]: r
                for r in repo.rows(
                    BALANCES + " WHERE b.id=ANY(:ids) AND b.entity_id=:e AND b.warehouse_id=:w "
                    "ORDER BY b.id",
                    {
                        "ids": sorted(r.balance_id for r in body.lines),
                        "e": body.entity_id,
                        "w": body.source_warehouse_id,
                    },
                )
            }
            if len(rows) != len(body.lines):
                raise BusinessError("TRANSFER_SCOPE", "请选择调出仓内已有成本记录的批次库存", 422)
            tid = uuid4()
            number = document_number("TR", tid)
            repo.execute(
                """INSERT INTO med.transfer(tenant_id,id,entity_id,source_warehouse_id,
              destination_warehouse_id,destination_location_id,number,reason,created_by)
              VALUES(:t,:id,:e,:s,:d,:l,:n,:reason,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": tid,
                    "e": body.entity_id,
                    "s": body.source_warehouse_id,
                    "d": body.destination_warehouse_id,
                    "l": body.destination_location_id,
                    "n": number,
                    "reason": body.reason,
                    "a": ctx.principal_id,
                },
            )
            for n, line in enumerate(body.lines, 1):
                row = rows[line.balance_id]
                self.eligible(repo, row)
                qty = self.qty(line.qty, row["qty_scale"])
                if qty > row["on_hand"] - row["reserved"] - row["blocked"]:
                    raise BusinessError("INSUFFICIENT_STOCK", "申请数量超过当前可用库存", 409)
                repo.execute(
                    """INSERT INTO med.transfer_line(tenant_id,id,transfer_id,line_no,
                  balance_id,qty,product_snapshot,package_snapshot,created_by)
                  VALUES(:t,:id,:tr,:n,:b,:q,CAST(:product AS jsonb),CAST(:package AS jsonb),:a)""",
                    {
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "tr": tid,
                        "n": n,
                        "b": line.balance_id,
                        "q": qty,
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
                "transfer",
                tid,
                "inventory.transfer_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(tid), "number": number, "state": "DRAFT", "version": "1"}

        return self.finance.permanent(
            ctx,
            "transfers.create",
            str(body.source_warehouse_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    @staticmethod
    def qty(value: str, scale: int) -> Decimal:
        try:
            return quantity(value, sku_scale=scale)
        except ValueError as exc:
            raise BusinessError("QTY_SCALE_INVALID", "数量不符合商品精度", 422) from exc

    def transition(
        self, ctx: TenantContext, tid: UUID, body: Transition, key: str, review: bool = False
    ) -> dict[str, Any]:
        permission = "transfers.review" if review else "transfers.write"
        entity = self.scope(ctx, tid, permission)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, permission)
            doc = one(repo, "SELECT * FROM med.transfer WHERE id=:id FOR UPDATE", {"id": tid})
            version(doc, body.expected_version)
            if doc["state"] not in (("DRAFT",) if review else ("DRAFT", "APPROVED")):
                raise BusinessError("INVALID_STATE", "仅未发出的调拨可审核或取消", 409)
            state = "CANCELLED"
            if review:
                assert isinstance(body, TransferReview)
                if doc["created_by"] == ctx.principal_id:
                    raise BusinessError("MAKER_CHECKER", "调拨必须由其他人员独立审核", 403)
                state = "APPROVED" if body.decision == "APPROVE" else "CANCELLED"
                repo.execute(
                    """UPDATE med.transfer SET reviewed_by=:a,reviewed_at=clock_timestamp(),
                  review_note=:note WHERE id=:id""",
                    {"id": tid, "a": ctx.principal_id, "note": body.reason},
                )
            repo.execute(
                """UPDATE med.transfer SET state=:state,version=version+1,
              cancel_note=CASE WHEN CAST(:state AS varchar)='CANCELLED' THEN :note ELSE NULL END
              WHERE id=:id""",
                {"id": tid, "state": state, "note": body.reason},
            )
            result = {"id": str(tid), "state": state, "version": str(doc["version"] + 1)}
            audit(
                repo,
                ctx,
                "transfer",
                tid,
                "inventory.transfer_reviewed" if review else "inventory.transfer_cancelled",
                {**result, "reason": body.reason},
            )
            return result

        return command(
            self.uow,
            ctx,
            "transfers.review" if review else "transfers.cancel",
            str(tid),
            key,
            body.model_dump(mode="json"),
            run,
        )
