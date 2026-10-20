"""Release eligible expired reservations and their source workflow atomically."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from medinv.modules.inventory.api.schemas import ReleaseRequest
from medinv.modules.inventory.application.service import InventoryService
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.tenancy.context import TenantContext

START = (datetime.min.replace(tzinfo=UTC), UUID(int=0))
REASON = "系统自动回收：预占到期且未付款、未拣货"


class ReservationExpiry:
    def __init__(self, inventory: InventoryService):
        self.inventory = inventory
        self.uow = inventory.uow

    def candidates(
        self, ctx: TenantContext, after: tuple[datetime, UUID] = START, limit: int = 25
    ) -> list[dict[str, Any]]:
        ctx.require("inventory.expire")
        if not 1 <= limit <= 100:
            raise ValueError("batch size must be between 1 and 100")
        with self.uow.open(ctx, readonly=True) as session:
            return SqlSession(session).rows(
                """
              SELECT r.id,r.entity_id,r.expires_at FROM med.reservation r
              JOIN med.legal_entity e ON (e.tenant_id,e.id)=(r.tenant_id,r.entity_id)
              JOIN med.inventory_demand d ON (d.tenant_id,d.id)=(r.tenant_id,r.source_id)
              LEFT JOIN med.shipment s ON (s.tenant_id,s.reservation_id)=(r.tenant_id,r.id)
              LEFT JOIN med.retail_sale p ON (p.tenant_id,p.reservation_id)=(r.tenant_id,r.id)
              WHERE r.state='ACTIVE' AND r.protected_until IS NULL
                AND r.expires_at<=clock_timestamp() AND e.status='ACTIVE'
                AND d.state<>'PAID_PROTECTED'
                AND (s.id IS NULL OR s.state='ALLOCATED')
                AND (r.source_type<>'RETAIL_SALE' OR p.state='RESERVED')
                AND NOT EXISTS (SELECT FROM med.retail_payment pay
                  WHERE pay.tenant_id=p.tenant_id AND pay.sale_id=p.id)
                AND (r.expires_at,r.id)>(:after,:id)
              ORDER BY r.expires_at,r.id LIMIT :n
            """,
                {"after": after[0], "id": after[1], "n": limit},
            )

    def expire(self, ctx: TenantContext, rid: UUID) -> bool:
        ctx.require("inventory.expire")
        with self.uow.open(ctx) as session:
            repo = SqlSession(session)
            hints = repo.rows("SELECT * FROM med.reservation WHERE id=:id", {"id": rid})
            if not hints:
                return False
            hint = hints[0]
            ctx.require("inventory.expire", hint["entity_id"])
            # Existing writers lock the entity first. Never invert reservation/payment locks.
            entities = repo.rows(
                "SELECT status FROM med.legal_entity WHERE id=:id FOR UPDATE SKIP LOCKED",
                {"id": hint["entity_id"]},
            )
            if not entities or entities[0]["status"] != "ACTIVE":
                return False
            orders = repo.rows(
                "SELECT id FROM med.sales_order WHERE id=:id FOR UPDATE", {"id": hint["source_id"]}
            )
            shipments = repo.rows(
                "SELECT * FROM med.shipment WHERE reservation_id=:id FOR UPDATE", {"id": rid}
            )
            sales = repo.rows(
                "SELECT * FROM med.retail_sale WHERE reservation_id=:id FOR UPDATE", {"id": rid}
            )
            demands = repo.rows(
                "SELECT * FROM med.inventory_demand WHERE id=:id FOR UPDATE",
                {"id": hint["source_id"]},
            )
            row = repo.rows(
                "SELECT *,expires_at<=clock_timestamp() AS expired "
                "FROM med.reservation WHERE id=:id FOR UPDATE",
                {"id": rid},
            )[0]
            if (
                row["state"] != "ACTIVE"
                or not row["expired"]
                or row["protected_until"] is not None
                or not demands
                or demands[0]["state"] == "PAID_PROTECTED"
            ):
                return False
            # Picked/verified goods require a human to put them back before release.
            if shipments and shipments[0]["state"] != "ALLOCATED":
                return False
            if row["source_type"] == "RETAIL_SALE" and (
                not sales or sales[0]["state"] != "RESERVED"
            ):
                return False
            if sales and (
                sales[0]["state"] != "RESERVED"
                or repo.rows(
                    "SELECT id FROM med.retail_payment WHERE sale_id=:id LIMIT 1",
                    {"id": sales[0]["id"]},
                )
            ):
                return False
            self.inventory._release(
                repo,
                ctx,
                rid,
                ReleaseRequest(expected_version=str(row["version"]), reason=REASON),
                retail=bool(sales),
                permission="inventory.expire",
            )
            repo.execute(
                "UPDATE med.reservation SET auto_released_at=clock_timestamp() WHERE id=:id",
                {"id": rid},
            )
            if shipments:
                repo.execute(
                    "UPDATE med.shipment SET state='CANCELLED',version=version+1 WHERE id=:id",
                    {"id": shipments[0]["id"]},
                )
                audit(
                    repo,
                    ctx,
                    "shipment",
                    shipments[0]["id"],
                    "sales.shipment_expired",
                    {"reservation_id": str(rid), "reason": REASON},
                )
            if orders:
                # Approved order/credit exposure remains; the user can allocate a new shipment.
                repo.execute(
                    "UPDATE med.sales_order SET version=version+1 WHERE id=:id",
                    {"id": orders[0]["id"]},
                )
            if sales:
                repo.execute(
                    "UPDATE med.retail_sale SET state='CANCELLED',version=version+1 WHERE id=:id",
                    {"id": sales[0]["id"]},
                )
                repo.execute(
                    "UPDATE med.inventory_demand SET state='CANCELLED' WHERE id=:id",
                    {"id": row["source_id"]},
                )
                audit(
                    repo,
                    ctx,
                    "retail_sale",
                    sales[0]["id"],
                    "retail.expired",
                    {"reservation_id": str(rid), "reason": REASON},
                )
            audit(
                repo,
                ctx,
                "reservation",
                rid,
                "inventory.auto_expired",
                {
                    "source_type": row["source_type"],
                    "source_id": str(row["source_id"]),
                    "expires_at": row["expires_at"].isoformat(),
                    "reason": REASON,
                },
            )
            return True
