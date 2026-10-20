import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from medinv.modules.inventory.api.schemas import ReserveRequest
from medinv.modules.inventory.application.service import InventoryService
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork, check_runtime_role
from medinv.platform.tenancy.context import TenantContext


@pytest.fixture(scope="session")
def settings():
    url = os.environ.get("MED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("MED_TEST_DATABASE_URL is required for real PostgreSQL integration tests")
    return Settings(
        database_url=SecretStr(url),
        control_database_url=SecretStr(os.environ["MED_CONTROL_DATABASE_URL"]),
        inventory_commands_enabled=True,
        pool_size=5,
        pool_overflow=20,
        lock_timeout_ms=10000,
        statement_timeout_ms=30000,
    )


@pytest.fixture(scope="session")
def engine(settings):
    engine = create_engine(
        settings.database_url.get_secret_value(), pool_size=5, max_overflow=20, hide_parameters=True
    )
    check_runtime_role(engine, "med")
    with engine.connect() as conn:
        version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
        assert 180000 <= version < 190000
    yield engine
    engine.dispose()


@dataclass
class World:
    ctx: TenantContext
    uow: TenantUnitOfWork
    service: InventoryService
    entity: UUID
    warehouse: UUID
    location: UUID
    sku: UUID
    lot: UUID
    balance: UUID

    def execute(self, sql, **params):
        with self.uow.open(self.ctx) as session:
            session.execute(text(sql), params)

    def scalar(self, sql, **params):
        with self.uow.open(self.ctx, readonly=True) as session:
            return session.execute(text(sql), params).scalar_one()

    def demand(self, qty="10", sku=None):
        source, line = uuid4(), uuid4()
        with self.uow.open(self.ctx) as session:
            session.execute(
                text("""INSERT INTO med.inventory_demand
                (tenant_id,id,entity_id,warehouse_id,source_type,state,checks_completed,
                eligible_until)
                VALUES (:tenant,:id,:entity,:warehouse,'SALES_ORDER','APPROVED',true,
                        now()+interval '1 day')"""),
                {
                    "tenant": self.ctx.tenant_id,
                    "id": source,
                    "entity": self.entity,
                    "warehouse": self.warehouse,
                },
            )
            session.execute(
                text("""INSERT INTO med.inventory_demand_line
                (tenant_id,id,demand_id,sku_id,requested_qty)
                VALUES (:tenant,:id,:source,:sku,CAST(:qty AS numeric))"""),
                {
                    "tenant": self.ctx.tenant_id,
                    "id": line,
                    "source": source,
                    "sku": sku or self.sku,
                    "qty": qty,
                },
            )
        return ReserveRequest.model_validate(
            {
                "client_operation_id": str(uuid4()),
                "entity_id": str(self.entity),
                "warehouse_id": str(self.warehouse),
                "source_type": "SALES_ORDER",
                "source_id": str(source),
                "expected_source_version": "1",
                "lines": [
                    {"source_line_id": str(line), "sku_id": str(sku or self.sku), "base_qty": qty}
                ],
            }
        )

    def add_balance(self, quantity="10", *, days=60, quality="QUALIFIED", sku=None):
        lot, balance = uuid4(), uuid4()
        params = {
            "tenant": self.ctx.tenant_id,
            "actor": self.ctx.principal_id,
            "entity": self.entity,
            "warehouse": self.warehouse,
            "location": self.location,
            "sku": sku or self.sku,
            "lot": lot,
            "balance": balance,
            "number": str(lot),
            "expiry": datetime.now(UTC) + timedelta(days=days),
            "qty": quantity,
            "quality": quality,
        }
        with self.uow.open(self.ctx) as session:
            session.execute(
                text("""INSERT INTO med.lot
                (tenant_id,id,created_by,updated_by,sku_id,manufacturer_key,lot_no,
                expiry_kind,expiry_exclusive)
                VALUES (:tenant,:lot,:actor,:actor,:sku,'TEST_FACTORY',:number,'DATE',
                :expiry)"""),
                params,
            )
            session.execute(
                text("""INSERT INTO med.stock_balance
                (tenant_id,id,created_by,updated_by,entity_id,owner_id,warehouse_id,
                location_id,
                 sku_id,lot_id,quality_status,on_hand)
                VALUES (:tenant,:balance,:actor,:actor,:entity,:entity,:warehouse,:location,
                        :sku,:lot,:quality,CAST(:qty AS numeric))"""),
                params,
            )
        return lot, balance


@pytest.fixture
def world(engine, settings):
    tenant, actor, entity, warehouse, location, product, sku = [uuid4() for _ in range(7)]
    ctx = TenantContext(
        tenant,
        actor,
        uuid4(),
        1,
        1,
        frozenset([entity]),
        frozenset(["inventory.read", "inventory.reserve", "inventory.release"]),
    )
    uow = TenantUnitOfWork(engine, settings)
    p = {
        "tenant": tenant,
        "actor": actor,
        "entity": entity,
        "warehouse": warehouse,
        "location": location,
        "product": product,
        "sku": sku,
        "gate": uuid4(),
    }
    with uow.open(ctx) as session:
        statements = [
            """INSERT INTO med.legal_entity
                (tenant_id,id,created_by,updated_by,name,credit_code,status)
               VALUES (:tenant,:entity,:actor,:actor,'测试主体','TEST','ACTIVE')""",
            """INSERT INTO med.warehouse
                (tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
               VALUES (:tenant,:warehouse,:actor,:actor,:entity,'WH1','PHYSICAL','测试仓库')""",
            """INSERT INTO med.location(tenant_id,id,created_by,updated_by,warehouse_id,code)
               VALUES (:tenant,:location,:actor,:actor,:warehouse,'L1')""",
            """INSERT INTO med.product(tenant_id,id,created_by,updated_by,code,name,category,
                approval_state,manufacturer_name)
               VALUES (:tenant,:product,:actor,:actor,'P1','测试器械','DEVICE','APPROVED',
                'TEST_FACTORY')""",
            """INSERT INTO med.sku
                (tenant_id,id,created_by,updated_by,product_id,code,base_unit,qty_scale)
               VALUES (:tenant,:sku,:actor,:actor,:product,'S1','个',0)""",
            """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id)
               VALUES (:tenant,:gate,:actor,:actor,:entity,:sku)""",
        ]
        for sql in statements:
            session.execute(text(sql), p)
    world = World(
        ctx, uow, InventoryService(uow), entity, warehouse, location, sku, uuid4(), uuid4()
    )
    world.lot, world.balance = world.add_balance()
    return world
