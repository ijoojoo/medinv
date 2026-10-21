from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from secrets import compare_digest
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SessionStore:
    """Read-only core API adapter. Session issuance belongs to the control API."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def authenticate(
        self,
        token: str | None,
        request_id: UUID,
        generation: str | None,
        csrf: str | None = None,
        *,
        write: bool = False,
        context_endpoint: bool = False,
    ) -> TenantContext:
        with self.engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            return self._authenticate(
                conn, token, request_id, generation, csrf, write, context_endpoint
            )

    @contextmanager
    def guard(
        self, token: str | None, request_id: UUID, generation: str | None, csrf: str | None
    ) -> Iterator[TenantContext]:
        if not token or len(token) > 512:
            raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='2000ms'"))
            conn.execute(text("SET LOCAL statement_timeout='5000ms'"))
            conn.execute(text("SET LOCAL idle_in_transaction_session_timeout='15s'"))
            locked = conn.execute(
                text("SELECT control.lock_business_session(:h)"),
                {"h": sha256(token.encode()).hexdigest()},
            ).scalar_one()
            if not locked:
                raise BusinessError("CONTEXT_STALE", "会话上下文变化，请刷新", 409)
            ctx = self._authenticate(conn, token, request_id, generation, csrf, True, False)

            def probe() -> None:
                conn.execute(text("SELECT 1"))

            marker = before_business_commit.set(probe)
            try:
                yield ctx
            finally:
                before_business_commit.reset(marker)

    def _authenticate(
        self,
        conn: Connection,
        token: str | None,
        request_id: UUID,
        generation: str | None,
        csrf: str | None,
        write: bool,
        context_endpoint: bool,
    ) -> TenantContext:
        if not token or len(token) > 512:
            raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
        digest = sha256(token.encode()).hexdigest()
        conn.execute(text("SET LOCAL statement_timeout='3000ms'"))
        row = (
            conn.execute(
                text("""
            SELECT s.*, m.entity_ids, m.permissions, m.role_codes
            FROM control.web_session s JOIN control.membership m
              USING (tenant_id,principal_id)
            JOIN control.tenant t ON t.id=s.tenant_id
            WHERE t.active AND s.token_hash=:token AND s.revoked_at IS NULL
              AND s.expires_at>clock_timestamp() AND m.active
              AND s.permission_version=m.permission_version
              AND (s.token_expires_at IS NULL OR s.token_expires_at>clock_timestamp())
        """),
                {"token": digest},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BusinessError("UNAUTHENTICATED", "会话已失效，请重新登录", 401)
        if not context_endpoint and generation != str(row["context_generation"]):
            raise BusinessError("CONTEXT_STALE", "操作上下文已变更，请刷新", 409)
        if write and (
            not csrf
            or len(csrf) > 512
            or not compare_digest(sha256(csrf.encode()).hexdigest(), row["csrf_hash"])
        ):
            raise BusinessError("CSRF_REJECTED", "请求校验失败", 403)
        return TenantContext(
            row["tenant_id"],
            row["principal_id"],
            request_id,
            row["context_generation"],
            row["permission_version"],
            frozenset(row["entity_ids"]),
            frozenset(row["permissions"]),
            frozenset(row["role_codes"]),
        )
