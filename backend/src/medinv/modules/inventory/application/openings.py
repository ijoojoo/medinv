import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.openings import OpeningCreate, OpeningReview
from medinv.modules.inventory.domain.decimal_values import quantity
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import (
    ZONE,
    document_number,
    encode,
    one,
    version,
)
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

UNUSED = """NOT EXISTS (SELECT 1 FROM med.stock_balance b
    WHERE (b.tenant_id,b.warehouse_id)=(w.tenant_id,w.id))
    AND NOT EXISTS (SELECT 1 FROM med.receipt r JOIN med.purchase_order o
      ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
      WHERE (o.tenant_id,o.warehouse_id)=(w.tenant_id,w.id))"""
PRODUCT_FIELDS = ("name", "specification", "manufacturer_name", "approval_number")


class OpeningService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    def scope(self, ctx: TenantContext, oid: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            entity = one(
                SqlSession(session),
                "SELECT entity_id FROM med.inventory_opening WHERE id=:id",
                {"id": oid},
            )["entity_id"]
        ctx.require(permission, entity)
        return UUID(str(entity))

    def unused(self, repo: SqlSession, entity: UUID, warehouse: UUID) -> None:
        row = one(
            repo,
            f"SELECT w.*,({UNUSED}) AS unused FROM med.warehouse w "
            "WHERE id=:w AND entity_id=:e FOR UPDATE OF w",
            {"w": warehouse, "e": entity},
        )
        if row["kind"] != "PHYSICAL" or row["archived_at"] or not row["unused"]:
            raise BusinessError(
                "WAREHOUSE_ALREADY_USED", "仅未发生库存业务或到货的实体仓库可登记期初", 409
            )

    def options(self, ctx: TenantContext, entity: UUID, offset: int) -> dict[str, Any]:
        ctx.require("inventory.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            warehouses = repo.rows(
                f"""SELECT w.id,w.name FROM med.warehouse w WHERE entity_id=:e
              AND kind='PHYSICAL' AND archived_at IS NULL AND {UNUSED} AND NOT EXISTS (
                SELECT 1 FROM med.inventory_opening d WHERE
                (d.tenant_id,d.warehouse_id)=(w.tenant_id,w.id)
                AND state NOT IN ('CANCELLED','REJECTED')) ORDER BY name,id LIMIT 201
                OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            locations = repo.rows(
                """SELECT l.id,l.code,l.warehouse_id FROM med.location l
              JOIN med.warehouse w ON (w.tenant_id,w.id)=(l.tenant_id,l.warehouse_id)
              WHERE w.entity_id=:e AND w.kind='PHYSICAL'
              AND w.archived_at IS NULL ORDER BY l.code,l.id LIMIT 201
              OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            skus = repo.rows(
                """SELECT s.id,s.code,s.base_unit,s.qty_scale,p.name
              FROM med.sku s JOIN med.product p
              ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id) WHERE
              p.approval_state='APPROVED'
              AND NOT s.serial_required AND p.restricted_category IS NULL
              AND med.product_qualification_current(p)
              AND NOT EXISTS (SELECT 1 FROM med.stock_gate g WHERE g.entity_id=:e AND
              g.sku_id=s.id AND g.blocked)
              ORDER BY p.name,s.id LIMIT 201 OFFSET :offset""",
                {"e": entity, "offset": offset},
            )
            more = any(len(rows) > 200 for rows in (warehouses, locations, skus))
            return dict(
                encode(
                    {
                        "warehouses": warehouses[:200],
                        "locations": locations[:200],
                        "skus": skus[:200],
                        "has_more": more,
                        "next_offset": offset + 200 if more else None,
                    }
                )
            )

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
        ctx.require("inventory.read", entity)
        filters, params = document_filters(
            "d",
            "concat_ws(' ',d.number,w.name,d.source_ref,d.reason)",
            {
                "DRAFT": "d.state='DRAFT'",
                "APPROVED": "d.state='APPROVED'",
                "REJECTED": "d.state='REJECTED'",
                "CANCELLED": "d.state='CANCELLED'",
                "POSTED": "d.state='POSTED'",
            },
            search,
            state,
            date_from,
            date_to,
        )
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT d.*,w.name AS warehouse_name,count(*) OVER() AS total
              FROM med.inventory_opening d JOIN med.warehouse w ON
              (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
              WHERE d.entity_id=:e {filters}
                ORDER BY d.created_at DESC,d.id LIMIT 50 OFFSET :offset""",
                {"e": entity, "offset": offset, **params},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def lines(self, repo: SqlSession, oid: UUID) -> list[dict[str, Any]]:
        return repo.rows(
            """SELECT l.*,l.product_snapshot->>'name' AS name,
          l.package_snapshot->>'base_unit' AS base_unit,loc.code AS location_code
          FROM med.inventory_opening_line l JOIN med.location loc ON
          (loc.tenant_id,loc.id)=(l.tenant_id,l.location_id)
          WHERE opening_id=:id ORDER BY line_no""",
            {"id": oid},
        )

    def detail(self, ctx: TenantContext, oid: UUID) -> dict[str, Any]:
        self.scope(ctx, oid, "inventory.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            doc = one(
                repo,
                """SELECT d.*,w.name AS warehouse_name FROM med.inventory_opening d
              JOIN med.warehouse w ON (w.tenant_id,w.id)=(d.tenant_id,d.warehouse_id)
              WHERE d.id=:id""",
                {"id": oid},
            )
            lines = self.lines(repo, oid)
            return dict(
                encode(
                    {
                        "document": {**doc, "version": str(doc["version"])},
                        "lines": lines,
                        "cost_total": sum((line["cost_amount"] for line in lines), Decimal(0)),
                    }
                )
            )

    def goods(
        self, repo: SqlSession, ctx: TenantContext, entity: UUID, lines: list[dict[str, Any]]
    ) -> dict[UUID, dict[str, Any]]:
        ids = sorted({line["sku_id"] for line in lines})
        rows = repo.rows(
            """SELECT s.*,p.name,p.specification,p.manufacturer_name,p.approval_number,
          p.approval_state,p.restricted_category,p.valid_from,p.valid_until,
            med.product_qualification_current(p) AS qualification_current
          FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
          WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p""",
            {"ids": ids},
        )
        if len(rows) != len(ids):
            raise BusinessError("NOT_FOUND", "期初商品不存在或不可见", 404)
        day = self.finance.today(repo)
        for row in rows:
            if (
                row["serial_required"]
                or row["restricted_category"]
                or row["approval_state"] != "APPROVED"
                or not row["qualification_current"]
            ):
                raise BusinessError(
                    "PRODUCT_INELIGIBLE", "期初仅支持资质有效且已审核的普通批次商品", 422
                )
            repo.execute(
                """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id)
              VALUES(:t,:id,:a,:a,:e,:s) ON CONFLICT DO NOTHING""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "a": ctx.principal_id,
                    "e": entity,
                    "s": row["id"],
                },
            )
        gates = repo.rows(
            """SELECT blocked FROM med.stock_gate WHERE entity_id=:e AND sku_id=ANY(:ids) ORDER
            BY sku_id FOR SHARE""",
            {"e": entity, "ids": ids},
        )
        if any(row["blocked"] for row in gates):
            raise BusinessError("SKU_BLOCKED", "冻结商品不能期初启用", 409)
        result = {row["id"]: row for row in rows}
        for line in lines:
            sku = result[line["sku_id"]]
            try:
                quantity(format(Decimal(line["qty"]), "f"), sku_scale=sku["qty_scale"])
            except ValueError as exc:
                raise BusinessError("INVALID_QUANTITY", "期初数量不符合商品精度", 422) from exc
            if line["mfg_date"] > day or line["expiry_date"] < day:
                raise BusinessError("LOT_INELIGIBLE", "期初合格库存不得已过期或生产日期在未来", 422)
            if "product_snapshot" in line and (
                line["product_snapshot"] != {k: sku[k] for k in PRODUCT_FIELDS}
                or line["package_snapshot"]["base_unit"] != sku["base_unit"]
                or line["package_snapshot"]["qty_scale"] != sku["qty_scale"]
            ):
                raise BusinessError(
                    "OPENING_PRODUCT_CHANGED", "商品资料与期初清单不一致，请取消并重新核对建单", 409
                )
        return result

    def lot(
        self, repo: SqlSession, ctx: TenantContext, line: dict[str, Any], sku: dict[str, Any]
    ) -> UUID:
        expiry = datetime.combine(line["expiry_date"] + timedelta(days=1), time.min, tzinfo=ZONE)
        params = {
            "t": ctx.tenant_id,
            "id": uuid4(),
            "a": ctx.principal_id,
            "s": line["sku_id"],
            "m": sku["manufacturer_name"],
            "n": line["lot_no"],
            "d": line["mfg_date"],
            "x": expiry,
            "raw": line["expiry_date"].isoformat(),
        }
        repo.execute(
            """INSERT INTO med.lot(tenant_id,id,created_by,updated_by,sku_id,manufacturer_key,
          lot_no,mfg_date,expiry_kind,expiry_exclusive,raw_expiry)
          VALUES(:t,:id,:a,:a,:s,:m,:n,:d,'DATE',:x,:raw) ON CONFLICT DO NOTHING""",
            params,
        )
        lot = one(
            repo,
            "SELECT * FROM med.lot WHERE sku_id=:s AND manufacturer_key=:m "
            "AND lot_no=:n FOR UPDATE",
            params,
        )
        if (
            lot["mfg_date"] != line["mfg_date"]
            or lot["expiry_kind"] != "DATE"
            or lot["expiry_exclusive"] != expiry
        ):
            raise BusinessError("LOT_IDENTITY_CONFLICT", "同批号生产日期或效期不一致", 409)
        if lot["quality_blocked"]:
            raise BusinessError("LOT_INELIGIBLE", "冻结批次不能期初启用", 409)
        return UUID(str(lot["id"]))

    def create(self, ctx: TenantContext, body: OpeningCreate, key: str) -> dict[str, Any]:
        ctx.require("opening.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "opening.write")
            self.unused(repo, body.entity_id, body.warehouse_id)
            if repo.rows(
                """SELECT id FROM med.inventory_opening WHERE warehouse_id=:w AND state NOT IN
                ('CANCELLED','REJECTED')""",
                {"w": body.warehouse_id},
            ):
                raise BusinessError("OPENING_EXISTS", "该仓库已有期初单，请先处理原单", 409)
            lines = [line.model_dump() for line in body.lines]
            goods = self.goods(repo, ctx, body.entity_id, lines)
            oid = uuid4()
            number = document_number("OP", oid)
            repo.execute(
                """INSERT INTO
med.inventory_opening(tenant_id,id,entity_id,warehouse_id,number,source_ref,reason,created_by)
              VALUES(:t,:id,:e,:w,:n,:ref,:reason,:a)""",
                {
                    "t": ctx.tenant_id,
                    "id": oid,
                    "e": body.entity_id,
                    "w": body.warehouse_id,
                    "n": number,
                    "ref": body.source_ref,
                    "reason": body.reason,
                    "a": ctx.principal_id,
                },
            )
            for index, line in enumerate(lines, 1):
                one(
                    repo,
                    "SELECT id FROM med.location WHERE id=:id AND warehouse_id=:w FOR SHARE",
                    {"id": line["location_id"], "w": body.warehouse_id},
                )
                sku = goods[line["sku_id"]]
                repo.execute(
                    """INSERT INTO med.inventory_opening_line
                    (tenant_id,id,opening_id,line_no,sku_id,
                  location_id,lot_no,mfg_date,expiry_date,qty,cost_amount,product_snapshot,package_snapshot)
                  VALUES(:t,:id,:o,:n,:sku_id,:location_id,:lot_no,:mfg_date,:expiry_date,:qty,:cost_amount,
                  CAST(:product AS jsonb),CAST(:package AS jsonb))""",
                    {
                        **line,
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "o": oid,
                        "n": index,
                        "product": json.dumps({k: sku[k] for k in PRODUCT_FIELDS}),
                        "package": json.dumps(
                            {
                                "base_unit": sku["base_unit"],
                                "qty_scale": sku["qty_scale"],
                                "numerator": 1,
                                "denominator": 1,
                            }
                        ),
                    },
                )
            audit(
                repo,
                ctx,
                "inventory_opening",
                oid,
                "inventory.opening_created",
                body.model_dump(mode="json"),
            )
            return {"id": str(oid), "number": number, "state": "DRAFT", "version": "1"}

        return self.finance.permanent(
            ctx,
            "opening.create",
            str(body.entity_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def transition(
        self, ctx: TenantContext, oid: UUID, body: Transition, key: str, review: bool = False
    ) -> dict[str, Any]:
        permission = "opening.review" if review else "opening.write"
        entity = self.scope(ctx, oid, permission)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, permission)
            doc = one(
                repo, "SELECT * FROM med.inventory_opening WHERE id=:id FOR UPDATE", {"id": oid}
            )
            version(doc, body.expected_version)
            if doc["state"] not in (["DRAFT"] if review else ["DRAFT", "APPROVED"]):
                raise BusinessError("INVALID_STATE", "当前期初单不能执行该操作", 409)
            state = "CANCELLED"
            if review:
                assert isinstance(body, OpeningReview)
                if ctx.principal_id == doc["created_by"]:
                    raise BusinessError("MAKER_CHECKER", "期初制单人不能审核本单", 403)
                state = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
                if state == "APPROVED":
                    self.unused(repo, entity, doc["warehouse_id"])
                    self.goods(repo, ctx, entity, self.lines(repo, oid))
                repo.execute(
                    """UPDATE med.inventory_opening SET reviewed_by=:a,
                    reviewed_at=clock_timestamp(),
                  review_note=:reason,evidence_ref=:ref WHERE id=:id""",
                    {
                        "id": oid,
                        "a": ctx.principal_id,
                        "reason": body.reason,
                        "ref": body.evidence_ref,
                    },
                )
            else:
                repo.execute(
                    "UPDATE med.inventory_opening SET cancel_reason=:reason WHERE id=:id",
                    {"id": oid, "reason": body.reason},
                )
            repo.execute(
                "UPDATE med.inventory_opening SET state=:state,version=version+1 WHERE id=:id",
                {"id": oid, "state": state},
            )
            audit(
                repo,
                ctx,
                "inventory_opening",
                oid,
                "inventory.opening_reviewed" if review else "inventory.opening_cancelled",
                body.model_dump(mode="json"),
            )
            return {"id": str(oid), "state": state, "version": str(doc["version"] + 1)}

        return command(
            self.uow,
            ctx,
            "opening.review" if review else "opening.cancel",
            str(oid),
            key,
            body.model_dump(mode="json"),
            run,
        )
