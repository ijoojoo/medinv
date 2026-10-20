import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from secrets import compare_digest, token_urlsafe
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from medinv.platform.auth.config import AuthSettings
from medinv.platform.auth.crypto import TokenCipher, digest
from medinv.platform.auth.provider import IdentityProvider
from medinv.platform.auth.roles import ROLE_NAMES, ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

logger = logging.getLogger("medinv.auth")


class AuthService:
    def __init__(self, engine: Engine, settings: AuthSettings, provider: IdentityProvider):
        self.engine, self.settings, self.provider = engine, settings, provider
        self.cipher = TokenCipher(settings.session_encryption_key.get_secret_value())

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout='5000ms'"))
            conn.execute(text("SET LOCAL lock_timeout='2000ms'"))
            yield conn

    @staticmethod
    def row(conn: Connection, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        result = conn.execute(text(sql), params).mappings().one_or_none()
        return dict(result) if result else None

    def begin_login(self) -> tuple[str, str]:
        state, browser, nonce, verifier = [token_urlsafe(48) for _ in range(4)]
        payload = self.cipher.seal({"nonce": nonce, "verifier": verifier})
        url = self.provider.authorization_url(state, nonce, verifier)
        with self.transaction() as conn:
            conn.execute(
                text("""INSERT INTO control.login_attempt
                (state_hash,browser_hash,sealed_payload,expires_at)
                VALUES (:state,:browser,:payload,clock_timestamp()+interval '10 minutes')"""),
                {"state": digest(state), "browser": digest(browser), "payload": payload},
            )
        return url, browser

    def finish_login(
        self, state: str, browser: str | None, code: str, previous_cookie: str | None = None
    ) -> str:
        if not browser or not 1 <= len(state) <= 512 or not 1 <= len(code) <= 4096:
            raise BusinessError("OIDC_STATE_INVALID", "登录请求已失效，请重新登录", 401)
        with self.transaction() as conn:
            attempt = self.row(
                conn,
                """UPDATE control.login_attempt
                SET consumed_at=clock_timestamp()
                WHERE state_hash=:state AND browser_hash=:browser AND consumed_at IS NULL
                  AND expires_at>clock_timestamp() RETURNING sealed_payload""",
                {"state": digest(state), "browser": digest(browser)},
            )
        if not attempt:
            raise BusinessError("OIDC_STATE_INVALID", "登录请求已失效，请重新登录", 401)
        saved = self.cipher.open(attempt["sealed_payload"])
        tokens = self.provider.exchange(code, saved["verifier"], saved["nonce"])
        cookie, csrf = token_urlsafe(48), token_urlsafe(48)
        tokens["csrf"] = csrf
        expires = self.token_expiry(tokens)
        with self.transaction() as conn:
            principal = self.row(
                conn,
                """SELECT * FROM control.principal
                WHERE issuer=:issuer AND subject=:subject""",
                {"issuer": self.settings.oidc_issuer_url, "subject": tokens["subject"]},
            )
            if not principal:
                raise BusinessError("NO_MEMBERSHIP", "该账号尚未加入企业，请联系企业管理员", 403)
            member = self.row(
                conn,
                """SELECT m.* FROM control.membership m
                JOIN control.tenant t ON t.id=m.tenant_id
                WHERE m.principal_id=:principal AND m.active AND t.active
                ORDER BY m.tenant_id LIMIT 1 FOR SHARE OF m,t""",
                {"principal": principal["id"]},
            )
            if not member:
                raise BusinessError("NO_MEMBERSHIP", "没有可用的企业成员身份", 403)
            conn.execute(
                text("""INSERT INTO control.web_session
                (token_hash,tenant_id,principal_id,csrf_hash,context_generation,
                 permission_version,expires_at,sealed_tokens,token_expires_at)
                VALUES (:hash,:tenant,:principal,:csrf,1,:version,
                  clock_timestamp()+make_interval(secs=>:ttl),:sealed,:expires)"""),
                {
                    "hash": digest(cookie),
                    "tenant": member["tenant_id"],
                    "principal": principal["id"],
                    "csrf": digest(csrf),
                    "version": member["permission_version"],
                    "ttl": self.settings.session_seconds,
                    "sealed": self.cipher.seal(tokens),
                    "expires": expires,
                },
            )
            if previous_cookie:
                conn.execute(
                    text("""UPDATE control.web_session SET revoked_at=clock_timestamp(),
                    sealed_tokens=NULL WHERE token_hash=:hash"""),
                    {"hash": digest(previous_cookie)},
                )
            self.audit(
                conn, member["tenant_id"], principal["id"], "session.login", principal["id"], {}
            )
        return cookie

    @staticmethod
    def token_expiry(tokens: dict[str, Any]) -> datetime:
        ttl = tokens.get("expires_in")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 86400:
            raise BusinessError("OIDC_TOKEN_INVALID", "身份服务返回了无效的令牌有效期", 401)
        if not tokens.get("access_token") or not tokens.get("refresh_token"):
            raise BusinessError("OIDC_TOKEN_INVALID", "身份令牌不完整", 401)
        return datetime.now(UTC) + timedelta(seconds=ttl)

    def session(
        self,
        conn: Connection,
        cookie: str | None,
        *,
        generation: str | None = None,
        csrf: str | None = None,
        write: bool = False,
        lock: bool = False,
        allow_expired_access: bool = False,
    ) -> dict[str, Any]:
        if not cookie or len(cookie) > 512:
            raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
        sql = """SELECT s.*, m.entity_ids,m.permissions,m.role_codes,
                   p.display_name,t.name AS tenant_name
            FROM control.web_session s
            JOIN control.membership m USING (tenant_id,principal_id)
            JOIN control.tenant t ON t.id=m.tenant_id
            JOIN control.principal p ON p.id=m.principal_id
            WHERE s.token_hash=:hash AND s.revoked_at IS NULL
              AND s.expires_at>clock_timestamp() AND m.active AND t.active
              AND s.permission_version=m.permission_version AND s.sealed_tokens IS NOT NULL"""
        if not allow_expired_access:
            sql += " AND s.token_expires_at>clock_timestamp()"
        if lock:
            sql += " FOR UPDATE OF s"
        row = self.row(conn, sql, {"hash": digest(cookie)})
        if not row:
            raise BusinessError("UNAUTHENTICATED", "会话已失效，请重新登录", 401)
        if write:
            if generation != str(row["context_generation"]):
                raise BusinessError("CONTEXT_STALE", "企业上下文已变更，请刷新页面", 409)
            if not csrf or len(csrf) > 512 or not compare_digest(digest(csrf), row["csrf_hash"]):
                raise BusinessError("CSRF_REJECTED", "请求校验失败", 403)
        return row

    def view(self, cookie: str | None) -> dict[str, Any]:
        with self.transaction() as conn:
            row = self.session(conn, cookie, allow_expired_access=True)
            tenants = (
                conn.execute(
                    text("""SELECT t.id,t.name FROM control.membership m
                JOIN control.tenant t ON t.id=m.tenant_id
                WHERE m.principal_id=:id AND m.active AND t.active ORDER BY t.name LIMIT 200"""),
                    {"id": row["principal_id"]},
                )
                .mappings()
                .all()
            )
            entities = (
                conn.execute(
                    text("""SELECT id,name FROM control.entity
                WHERE tenant_id=:tenant AND id=ANY(:ids) ORDER BY name"""),
                    {"tenant": row["tenant_id"], "ids": row["entity_ids"]},
                )
                .mappings()
                .all()
            )
        sealed = self.cipher.open(row["sealed_tokens"])
        return {
            "principal_id": str(row["principal_id"]),
            "display_name": row["display_name"],
            "tenant_id": str(row["tenant_id"]),
            "tenant_name": row["tenant_name"],
            "context_generation": str(row["context_generation"]),
            "permission_version": str(row["permission_version"]),
            "permissions": row["permissions"],
            "role_codes": row["role_codes"],
            "csrf_token": sealed["csrf"],
            "expires_at": row["expires_at"].isoformat(),
            "refresh_needed": row["token_expires_at"] <= datetime.now(UTC) + timedelta(seconds=60),
            "tenants": [{"id": str(t["id"]), "name": t["name"]} for t in tenants],
            "entities": [{"id": str(e["id"]), "name": e["name"]} for e in entities],
        }

    @staticmethod
    def audit(
        conn: Connection,
        tenant: UUID,
        actor: UUID,
        action: str,
        target: UUID,
        detail: dict[str, Any],
    ) -> None:
        conn.execute(
            text("""INSERT INTO control.audit_event
            (id,tenant_id,actor_id,action,target_id,detail)
            VALUES (:id,:tenant,:actor,:action,:target,CAST(:detail AS jsonb))"""),
            {
                "id": uuid4(),
                "tenant": tenant,
                "actor": actor,
                "action": action,
                "target": target,
                "detail": json.dumps(detail),
            },
        )

    def switch_tenant(
        self, cookie: str, generation: str, csrf: str, tenant: UUID
    ) -> dict[str, Any]:
        with self.transaction() as conn:
            hint = self.session(conn, cookie, generation=generation, csrf=csrf, write=True)
            conn.execute(
                text("SELECT id FROM control.tenant WHERE id=ANY(:ids) ORDER BY id FOR UPDATE"),
                {"ids": sorted({hint["tenant_id"], tenant})},
            )
            row = self.session(
                conn, cookie, generation=generation, csrf=csrf, write=True, lock=True
            )
            target = self.row(
                conn,
                """SELECT m.* FROM control.membership m
                JOIN control.tenant t ON t.id=m.tenant_id WHERE m.tenant_id=:tenant
                AND m.principal_id=:principal AND m.active AND t.active""",
                {"tenant": tenant, "principal": row["principal_id"]},
            )
            if not target:
                raise BusinessError("TENANT_FORBIDDEN", "未加入该企业或企业已停用", 403)
            if row["refresh_id"] is not None:
                raise BusinessError(
                    "SESSION_REFRESH_BUSY", "会话正在刷新，请稍后重试", 503, retryable=True
                )
            tokens = self.cipher.open(row["sealed_tokens"])
            tokens["csrf"] = token_urlsafe(48)
            conn.execute(
                text("""UPDATE control.web_session SET tenant_id=:tenant,
                context_generation=context_generation+1,permission_version=:version,
                csrf_hash=:csrf,sealed_tokens=:sealed WHERE token_hash=:hash"""),
                {
                    "tenant": tenant,
                    "version": target["permission_version"],
                    "csrf": digest(tokens["csrf"]),
                    "sealed": self.cipher.seal(tokens),
                    "hash": row["token_hash"],
                },
            )
            self.audit(conn, tenant, row["principal_id"], "session.switch_tenant", tenant, {})
        return self.view(cookie)

    def refresh(self, cookie: str, generation: str, csrf: str) -> dict[str, Any]:
        refresh_id = uuid4()
        abandoned = False
        already_fresh = False
        with self.transaction() as conn:
            row = self.session(
                conn,
                cookie,
                generation=generation,
                csrf=csrf,
                write=True,
                lock=True,
                allow_expired_access=True,
            )
            if row["refresh_id"] is not None:
                if row["refresh_until"] > datetime.now(UTC):
                    raise BusinessError(
                        "SESSION_REFRESH_BUSY", "会话正在刷新，请稍后重试", 503, retryable=True
                    )
                # A crashed worker may have already spent a rotating refresh token. Never reuse it.
                conn.execute(
                    text("""UPDATE control.web_session SET revoked_at=clock_timestamp(),
                    sealed_tokens=NULL,refresh_id=NULL,refresh_until=NULL
                    WHERE token_hash=:hash"""),
                    {"hash": row["token_hash"]},
                )
                abandoned = True
            elif row["token_expires_at"] > datetime.now(UTC) + timedelta(seconds=60):
                already_fresh = True
            else:
                conn.execute(
                    text("""UPDATE control.web_session SET refresh_id=:refresh,
                    refresh_until=clock_timestamp()+interval '20 seconds'
                    WHERE token_hash=:hash"""),
                    {"refresh": refresh_id, "hash": row["token_hash"]},
                )
        if already_fresh:
            return self.view(cookie)
        if abandoned:
            raise BusinessError("REAUTHENTICATE", "刷新结果不确定，请重新登录", 401)
        try:
            tokens = self.provider.refresh(self.cipher.open(row["sealed_tokens"]))
            expires = self.token_expiry(tokens)
        except Exception as exc:
            self.fail_refresh(row["token_hash"], refresh_id)
            raise BusinessError("REAUTHENTICATE", "会话刷新失败，请重新登录", 401) from exc
        with self.transaction() as conn:
            updated = self.row(
                conn,
                """UPDATE control.web_session s SET sealed_tokens=:sealed,
                token_expires_at=:expires,refresh_id=NULL,refresh_until=NULL,
                token_version=token_version+1
                WHERE s.token_hash=:hash AND s.refresh_id=:refresh AND s.revoked_at IS NULL
                  AND s.context_generation=:generation AND s.expires_at>clock_timestamp()
                  AND s.refresh_until>clock_timestamp()
                  AND EXISTS (SELECT FROM control.membership m
                    JOIN control.tenant t ON t.id=m.tenant_id
                    WHERE (m.tenant_id,m.principal_id)=(s.tenant_id,s.principal_id)
                    AND m.active AND t.active AND m.permission_version=s.permission_version)
                RETURNING s.token_hash""",
                {
                    "sealed": self.cipher.seal(tokens),
                    "expires": expires,
                    "hash": row["token_hash"],
                    "refresh": refresh_id,
                    "generation": row["context_generation"],
                },
            )
        if not updated:
            self.fail_refresh(row["token_hash"], refresh_id)
            raise BusinessError("REAUTHENTICATE", "会话状态已变更，请重新登录", 401)
        return self.view(cookie)

    def fail_refresh(self, token_hash: str, refresh_id: UUID) -> None:
        with self.transaction() as conn:
            conn.execute(
                text("""UPDATE control.web_session SET revoked_at=clock_timestamp(),
                sealed_tokens=NULL,refresh_id=NULL,refresh_until=NULL
                WHERE token_hash=:hash AND refresh_id=:refresh"""),
                {"hash": token_hash, "refresh": refresh_id},
            )

    def logout(self, cookie: str, generation: str, csrf: str) -> None:
        with self.transaction() as conn:
            row = self.session(
                conn,
                cookie,
                generation=generation,
                csrf=csrf,
                write=True,
                lock=True,
                allow_expired_access=True,
            )
            conn.execute(
                text("""UPDATE control.web_session SET revoked_at=clock_timestamp(),
                sealed_tokens=NULL,refresh_id=NULL,refresh_until=NULL
                    WHERE token_hash=:hash"""),
                {"hash": row["token_hash"]},
            )
            self.audit(
                conn,
                row["tenant_id"],
                row["principal_id"],
                "session.logout",
                row["principal_id"],
                {},
            )
        try:
            self.provider.revoke(self.cipher.open(row["sealed_tokens"]))
        except Exception:
            # Local revocation is authoritative even when the IdP is unavailable.
            logger.warning("idp_revocation_unavailable")

    def members(self, cookie: str, generation: str) -> dict[str, Any]:
        with self.transaction() as conn:
            actor = self.session(conn, cookie)
            if generation != str(actor["context_generation"]):
                raise BusinessError("CONTEXT_STALE", "企业上下文已变更，请刷新页面", 409)
            if "members.read" not in actor["permissions"]:
                raise BusinessError("FORBIDDEN", "没有成员查看权限", 403)
            rows = (
                conn.execute(
                    text("""SELECT m.*,p.display_name FROM control.membership m
                JOIN control.principal p ON p.id=m.principal_id
                WHERE m.tenant_id=:tenant ORDER BY p.display_name LIMIT 201"""),
                    {"tenant": actor["tenant_id"]},
                )
                .mappings()
                .all()
            )
            if len(rows) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "成员数超过当前查询上限", 422)
            entities = (
                conn.execute(
                    text(
                        "SELECT id,name FROM control.entity WHERE tenant_id=:tenant ORDER BY name"
                    ),
                    {"tenant": actor["tenant_id"]},
                )
                .mappings()
                .all()
            )
        return {
            "items": [
                {
                    "principal_id": str(r["principal_id"]),
                    "display_name": r["display_name"],
                    "active": r["active"],
                    "role_codes": r["role_codes"],
                    "entity_ids": list(map(str, r["entity_ids"])),
                    "version": str(r["permission_version"]),
                }
                for r in rows
            ],
            "roles": [{"code": key, "name": value} for key, value in ROLE_NAMES.items()],
            "entities": [{"id": str(e["id"]), "name": e["name"]} for e in entities],
        }

    def change_member(
        self,
        cookie: str,
        generation: str,
        csrf: str,
        principal: UUID,
        expected: int,
        roles: list[str],
        entities: list[UUID],
        active: bool,
    ) -> None:
        if not roles or any(role not in ROLE_PERMISSIONS for role in roles):
            raise BusinessError("INVALID_ROLE", "角色不合法", 422)
        permissions = sorted(set().union(*(ROLE_PERMISSIONS[r] for r in roles)))
        with self.transaction() as conn:
            hint = self.session(conn, cookie, generation=generation, csrf=csrf, write=True)
            conn.execute(
                text("SELECT id FROM control.tenant WHERE id=:id FOR UPDATE"),
                {"id": hint["tenant_id"]},
            )
            actor = self.session(
                conn, cookie, generation=generation, csrf=csrf, write=True, lock=True
            )
            if "members.manage" not in actor["permissions"] or principal == actor["principal_id"]:
                raise BusinessError("FORBIDDEN", "没有修改该成员的权限", 403)
            target = self.row(
                conn,
                "SELECT * FROM control.membership WHERE tenant_id=:tenant "
                "AND principal_id=:id FOR UPDATE",
                {"tenant": actor["tenant_id"], "id": principal},
            )
            if not target:
                raise BusinessError("NOT_FOUND", "成员不可见或不存在", 404)
            if target["permission_version"] != expected:
                raise BusinessError("VERSION_CONFLICT", "成员权限已变更，请刷新", 409)
            if "OWNER" not in actor["role_codes"] and (
                set(roles) & {"OWNER", "ADMIN"}
                or set(target["role_codes"]) & {"OWNER", "ADMIN"}
                or not set(entities).issubset(actor["entity_ids"])
            ):
                raise BusinessError("FORBIDDEN", "不能授予超出自身管理范围的权限", 403)
            valid_entities = (
                conn.execute(
                    text("SELECT id FROM control.entity WHERE tenant_id=:tenant"),
                    {"tenant": actor["tenant_id"]},
                )
                .scalars()
                .all()
            )
            if not set(entities).issubset(valid_entities):
                raise BusinessError("INVALID_ENTITY", "主体不属于当前企业", 422)
            conn.execute(
                text("""UPDATE control.membership SET active=:active,role_codes=:roles,
                entity_ids=:entities,permissions=:permissions,permission_version=permission_version+1
                WHERE tenant_id=:tenant AND principal_id=:id"""),
                {
                    "active": active,
                    "roles": sorted(set(roles)),
                    "entities": sorted(set(entities)),
                    "permissions": permissions,
                    "tenant": actor["tenant_id"],
                    "id": principal,
                },
            )
            conn.execute(
                text("""UPDATE control.web_session SET revoked_at=clock_timestamp(),
                sealed_tokens=NULL,refresh_id=NULL,refresh_until=NULL
                WHERE tenant_id=:tenant AND principal_id=:id AND revoked_at IS NULL"""),
                {"tenant": actor["tenant_id"], "id": principal},
            )
            self.audit(
                conn,
                actor["tenant_id"],
                actor["principal_id"],
                "membership.changed",
                principal,
                {
                    "roles": sorted(set(roles)),
                    "entity_ids": sorted(map(str, entities)),
                    "active": active,
                },
            )
