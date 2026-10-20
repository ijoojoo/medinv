import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from medinv.platform.database.uow import TenantUnitOfWork

pytestmark = pytest.mark.integration


def test_runtime_is_fail_closed_without_context_and_cannot_ddl(engine, world):
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM med.legal_entity")).scalar_one() == 0
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE med.forbidden_table (id int)"))
    with pytest.raises(DBAPIError):
        with world.uow.open(world.ctx) as session:
            session.execute(text("SET ROLE med_owner"))


def test_rls_covers_every_business_table(engine):
    with engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
            WHERE n.nspname='med' AND c.relkind='r' AND c.relname<>'alembic_version'""")
        ).all()
    compatibility = json.loads(
        (Path(__file__).parents[3] / "deploy/trial/compatibility.json").read_text(encoding="utf-8")
    )
    assert len(rows) == compatibility["business_rls_tables"]
    assert "party_certificate" in {name for name, _, _ in rows}
    assert all(enabled and forced for _, enabled, forced in rows)


def test_same_connection_switches_tenants_without_leaks(world, settings):
    engine = create_engine(settings.database_url.get_secret_value(), pool_size=1, max_overflow=0)
    uow = TenantUnitOfWork(engine, settings)
    other = replace(world.ctx, tenant_id=uuid4())
    other_entity = uuid4()
    with uow.open(other) as session:
        session.execute(
            text("""INSERT INTO med.legal_entity
            (tenant_id,id,created_by,updated_by,name,credit_code,status)
            VALUES (:tenant,:id,:actor,:actor,'B','B','ACTIVE')"""),
            {"tenant": other.tenant_id, "id": other_entity, "actor": other.principal_id},
        )
    for ctx, expected in [
        (world.ctx, world.entity),
        (other, other_entity),
        (world.ctx, world.entity),
    ]:
        with uow.open(ctx, readonly=True) as session:
            assert session.execute(text("SELECT id FROM med.legal_entity")).scalars().all() == [
                expected
            ]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM med.legal_entity")).scalar_one() == 0
    with pytest.raises(DBAPIError):
        with uow.open(other) as session:
            session.execute(
                text("""INSERT INTO med.warehouse
              (tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
              VALUES (:tenant,:id,:actor,:actor,:foreign_entity,'BAD','PHYSICAL','跨租户失败')"""),
                {
                    "tenant": other.tenant_id,
                    "id": uuid4(),
                    "actor": other.principal_id,
                    "foreign_entity": world.entity,
                },
            )
    with pytest.raises(DBAPIError):
        with uow.open(world.ctx) as session:
            session.execute(
                text("""INSERT INTO med.legal_entity
              (tenant_id,id,created_by,updated_by,name,credit_code,status)
              VALUES (:other,:id,:actor,:actor,'BAD','BAD','ACTIVE')"""),
                {"other": other.tenant_id, "id": uuid4(), "actor": world.ctx.principal_id},
            )
    engine.dispose()


def test_readonly_cannot_mutate_and_audit_cannot_be_rewritten(world):
    with pytest.raises(DBAPIError):
        with world.uow.open(world.ctx, readonly=True) as session:
            session.execute(text("UPDATE med.stock_balance SET reserved=1"))
    world.service.reserve(world.ctx, world.demand("1"), str(uuid4()))
    with pytest.raises(DBAPIError):
        world.execute("UPDATE med.audit_event SET reason='rewritten'")
    with pytest.raises(DBAPIError):
        world.execute("DELETE FROM med.audit_event")
    assert world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 1


def test_rls_does_not_depend_on_application_where_clause(world):
    other = replace(world.ctx, tenant_id=uuid4())
    with world.uow.open(other) as session:
        rows = session.execute(
            text("UPDATE med.stock_balance SET reserved=on_hand RETURNING id")
        ).all()
        assert rows == []
    assert world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
