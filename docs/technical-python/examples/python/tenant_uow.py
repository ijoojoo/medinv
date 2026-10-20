"""Design reference only. Requires SQLAlchemy 2.0; not a complete service."""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

@dataclass(frozen=True, slots=True)
class TenantContext:
    # Construct only after server-side authentication and tenant membership checks.
    tenant_id: UUID
    principal_id: UUID
    request_id: UUID
    context_generation: int
    permission_version: int

class TenantUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        # One business Engine only. Never bind the control DB into this factory.
        self._sessions = sessionmaker(
            bind=engine, class_=Session, autoflush=False,
            expire_on_commit=False, autobegin=False,
        )

    @contextmanager
    def open(self, ctx: TenantContext, *, readonly: bool = False) -> Iterator[Session]:
        if not isinstance(ctx.tenant_id, UUID) or ctx.context_generation < 1 or ctx.permission_version < 1:
            raise ValueError("trusted tenant context required")
        # Must be opened, used and closed inside the same worker thread.
        with self._sessions() as session:
            with session.begin():
                if readonly:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant, true)"),
                    {"tenant": str(ctx.tenant_id)},
                )
                session.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": "500ms"},
                )
                session.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": "1500ms"},
                )
                session.execute(
                    text("SELECT set_config('idle_in_transaction_session_timeout', :timeout, true)"),
                    {"timeout": "5s"},
                )
                # Repositories must not commit, rollback, close or replace this Session.
                # No HTTP calls, no streaming responses and no parallel tasks here.
                yield session
            # commit has completed before the caller can return its HTTP response.
        # Failure propagates and rolls back; do not swallow DB exceptions here.
