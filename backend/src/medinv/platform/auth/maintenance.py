"""Internal maintenance identity; never constructed from an HTTP user's input."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

EXPIRY_ACTOR = UUID("518175a3-fcca-50e3-8dcb-68d4508031b3")


def expiry_context(tenant: UUID, entity: UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant,
        EXPIRY_ACTOR,
        uuid4(),
        1,
        1,
        frozenset([entity]) if entity else frozenset(),
        frozenset({"inventory.expire"}),
    )


class MaintenanceGuard:
    def __init__(self, engine: Engine):
        self.engine = engine

    def tenants(self, after: UUID, limit: int = 10) -> list[UUID]:
        with self.engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout='3000ms'"))
            return list(
                conn.execute(
                    text(
                        "SELECT id FROM control.tenant WHERE active AND id>:after "
                        "ORDER BY id LIMIT :n"
                    ),
                    {"after": after, "n": limit},
                ).scalars()
            )

    @contextmanager
    def hold(self, tenant: UUID) -> Iterator[None]:
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='1000ms'"))
            conn.execute(text("SET LOCAL statement_timeout='5000ms'"))
            conn.execute(text("SET LOCAL idle_in_transaction_session_timeout='15s'"))
            active = conn.execute(
                text("SELECT control.lock_maintenance_tenant(:id)"), {"id": tenant}
            ).scalar_one()
            if not active:
                raise BusinessError("TENANT_UNAVAILABLE", "企业已停用，暂停自动回收", 409)

            def probe() -> None:
                conn.execute(text("SELECT 1"))

            marker = before_business_commit.set(probe)
            try:
                yield
            finally:
                before_business_commit.reset(marker)
