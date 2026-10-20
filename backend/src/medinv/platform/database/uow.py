from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from medinv.platform.config import Settings
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.tenancy.context import TenantContext


def make_engine(url: str, settings: Settings) -> Engine:
    return create_engine(
        url,
        pool_size=settings.pool_size,
        max_overflow=settings.pool_overflow,
        pool_timeout=5,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def check_runtime_role(engine: Engine, schema: str) -> None:
    with engine.connect() as connection:
        role = (
            connection.execute(
                text("""
            SELECT r.rolsuper, r.rolbypassrls,
                   pg_has_role(current_user, n.nspowner, 'MEMBER') AS owns_schema
            FROM pg_roles r CROSS JOIN pg_namespace n
            WHERE r.rolname=current_user AND n.nspname=:schema
        """),
                {"schema": schema},
            )
            .mappings()
            .one_or_none()
        )
        if role is None or any(role.values()):
            raise RuntimeError("Database runtime role must not own schema or bypass RLS")


class TenantUnitOfWork:
    def __init__(self, engine: Engine, settings: Settings):
        self.settings = settings
        self._sessions = sessionmaker(
            engine,
            autoflush=False,
            expire_on_commit=False,
            autobegin=False,
        )

    @contextmanager
    def open(
        self, ctx: TenantContext, *, readonly: bool = False, repeatable: bool = False
    ) -> Iterator[Session]:
        if (
            not isinstance(ctx.tenant_id, UUID)
            or not isinstance(ctx.principal_id, UUID)
            or ctx.context_generation < 1
            or ctx.permission_version < 1
        ):
            raise ValueError("trusted tenant context required")
        with self._sessions() as session, session.begin():
            if repeatable:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            if readonly:
                session.execute(text("SET TRANSACTION READ ONLY"))
            values = {
                "app.tenant_id": str(ctx.tenant_id),
                "lock_timeout": f"{self.settings.lock_timeout_ms}ms",
                "statement_timeout": f"{self.settings.statement_timeout_ms}ms",
                "idle_in_transaction_session_timeout": "5s",
            }
            for key, value in values.items():
                session.execute(
                    text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value}
                )
            yield session
            guard = before_business_commit.get()
            if not readonly and guard is not None:
                guard()
