import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from medinv.entrypoints.control_api import create_app
from medinv.platform.auth.config import AuthSettings
from medinv.platform.auth.crypto import digest
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.auth.service import AuthService
from medinv.platform.auth.sessions import SessionStore
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


class FakeProvider:
    def __init__(self, subject):
        self.subject = subject
        self.calls = 0
        self.started = Event()
        self.resume = Event()
        self.resume.set()

    def authorization_url(self, state, nonce, verifier):
        return "https://idp.example/authorize?" + urlencode({"state": state})

    def exchange(self, code, verifier, nonce):
        return {
            "subject": self.subject,
            "nonce": nonce,
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "expires_in": 300,
        }

    def refresh(self, tokens):
        self.calls += 1
        self.started.set()
        assert self.resume.wait(timeout=5)
        time.sleep(0.2)
        return {**tokens, "refresh_token": "rotated-private-refresh", "expires_in": 300}

    def revoke(self, tokens):
        pass


@pytest.fixture
def auth():
    config = AuthSettings()  # environment supplied by scripts/dev.py
    engine = create_engine(
        config.control_auth_database_url.get_secret_value(),
        pool_size=5,
        max_overflow=20,
        hide_parameters=True,
    )
    owner = create_engine(os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True)
    tenant, second, principal, target, entity, entity2 = [uuid4() for _ in range(6)]
    subject = str(uuid4())
    target_subject = str(uuid4())
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        for tid, eid in [(tenant, entity), (second, entity2)]:
            conn.execute(
                text("INSERT INTO control.tenant(id,name) VALUES (:id,'Auth test')"), {"id": tid}
            )
            conn.execute(
                text("INSERT INTO control.entity(tenant_id,id,name) VALUES (:tid,:id,'Entity')"),
                {"tid": tid, "id": eid},
            )
        for pid, sub in [(principal, subject), (target, target_subject)]:
            conn.execute(
                text("""INSERT INTO control.principal(id,issuer,subject,display_name)
                VALUES (:id,:issuer,:subject,'Test member')"""),
                {"id": pid, "issuer": config.oidc_issuer_url, "subject": sub},
            )
        for tid, pid, eid, role, perms in [
            (
                tenant,
                principal,
                entity,
                "OWNER",
                ["members.read", "members.manage", "inventory.read"],
            ),
            (
                second,
                principal,
                entity2,
                "OWNER",
                ["members.read", "members.manage", "inventory.read"],
            ),
            (tenant, target, entity, "VIEWER", ["inventory.read"]),
        ]:
            conn.execute(
                text("""INSERT INTO control.membership
                (tenant_id,principal_id,entity_ids,permissions,role_codes)
                VALUES (:t,:p,:e,:perms,:roles)"""),
                {"t": tid, "p": pid, "e": [eid], "perms": perms, "roles": [role]},
            )
    provider = FakeProvider(subject)
    service = AuthService(engine, config, provider)

    def login():
        url, browser = service.begin_login()
        state = parse_qs(urlsplit(url).query)["state"][0]
        return service.finish_login(state, browser, "code")

    cookie = login()
    view = service.view(cookie)
    if view["tenant_id"] != str(tenant):
        service.switch_tenant(cookie, view["context_generation"], view["csrf_token"], tenant)

    def execute(sql, **params):
        with owner.begin() as conn:
            conn.execute(text("SET ROLE control_owner"))
            return conn.execute(text(sql), params)

    obj = SimpleNamespace(
        config=config,
        engine=engine,
        owner=owner,
        tenant=tenant,
        second=second,
        principal=principal,
        target=target,
        target_subject=target_subject,
        entity=entity,
        entity2=entity2,
        service=service,
        provider=provider,
        cookie=cookie,
        login=login,
        execute=execute,
    )
    yield obj
    engine.dispose()
    owner.dispose()


def credentials(auth):
    v = auth.service.view(auth.cookie)
    return auth.cookie, v["context_generation"], v["csrf_token"]


def expire_access(auth):
    auth.execute(
        "UPDATE control.web_session SET token_expires_at=:expires WHERE token_hash=:hash",
        expires=datetime.now(UTC) - timedelta(seconds=1),
        hash=digest(auth.cookie),
    )


def test_state_is_browser_bound_single_use_and_secrets_encrypted(auth):
    url, browser = auth.service.begin_login()
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(BusinessError):
        auth.service.finish_login(state, "wrong-browser", "code")
    cookie = auth.service.finish_login(state, browser, "code")
    with pytest.raises(BusinessError):
        auth.service.finish_login(state, browser, "code")
    with auth.owner.connect() as conn:
        conn.execute(text("SET ROLE control_owner"))
        sealed = conn.execute(
            text("SELECT sealed_tokens FROM control.web_session WHERE token_hash=:h"),
            {"h": digest(cookie)},
        ).scalar_one()
    assert "private-refresh" not in sealed
    assert "private-access" not in sealed
    assert "access_token" not in auth.service.view(cookie)


def test_switch_rejects_old_tabs_and_unauthorized_tenants(auth):
    old = credentials(auth)
    with pytest.raises(BusinessError) as error:
        auth.service.switch_tenant(*old, uuid4())
    assert error.value.code == "TENANT_FORBIDDEN"
    updated = auth.service.switch_tenant(*old, auth.second)
    assert updated["tenant_id"] == str(auth.second)
    assert updated["csrf_token"] != old[2]
    with pytest.raises(BusinessError) as stale_members:
        auth.service.members(auth.cookie, old[1])
    assert stale_members.value.code == "CONTEXT_STALE"
    with pytest.raises(BusinessError) as error:
        auth.service.logout(*old)
    assert error.value.code == "CONTEXT_STALE"
    core = create_engine(os.environ["MED_CONTROL_DATABASE_URL"], hide_parameters=True)
    with pytest.raises(BusinessError) as error:
        SessionStore(core).authenticate(auth.cookie, uuid4(), old[1])
    assert error.value.code == "CONTEXT_STALE"
    core.dispose()


def test_refresh_serializes_across_instances(auth):
    creds = credentials(auth)
    expire_access(auth)
    other = AuthService(auth.engine, auth.config, auth.provider)

    def refresh(index):
        try:
            return (auth.service if index % 2 else other).refresh(*creds)["principal_id"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(refresh, range(10)))
    assert auth.provider.calls == 1
    assert all(value in {str(auth.principal), "SESSION_REFRESH_BUSY"} for value in results)
    assert str(auth.principal) in results


def test_crashed_refresh_does_not_reuse_potentially_spent_token(auth):
    creds = credentials(auth)
    expire_access(auth)
    auth.execute(
        """UPDATE control.web_session SET refresh_id=:rid,
        refresh_until=clock_timestamp()-interval '1 second' WHERE token_hash=:h""",
        rid=uuid4(),
        h=digest(auth.cookie),
    )
    with pytest.raises(BusinessError) as error:
        auth.service.refresh(*creds)
    assert error.value.code == "REAUTHENTICATE"
    assert auth.provider.calls == 0
    with pytest.raises(BusinessError):
        auth.service.view(auth.cookie)


def test_late_refresh_cannot_restore_logged_out_session(auth):
    creds = credentials(auth)
    expire_access(auth)
    auth.provider.resume.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(auth.service.refresh, *creds)
        assert auth.provider.started.wait(timeout=3)
        auth.service.logout(*creds)
        auth.provider.resume.set()
        with pytest.raises(BusinessError):
            future.result(timeout=5)
    with pytest.raises(BusinessError):
        auth.service.view(auth.cookie)


def test_role_update_is_scoped_and_revokes_sessions(auth):
    target_service = AuthService(auth.engine, auth.config, FakeProvider(auth.target_subject))
    url, browser = target_service.begin_login()
    state = parse_qs(urlsplit(url).query)["state"][0]
    target_cookie = target_service.finish_login(state, browser, "code")
    assert target_service.view(target_cookie)["principal_id"] == str(auth.target)
    auth.service.change_member(
        *credentials(auth), auth.target, 1, ["OPERATOR"], [auth.entity], True
    )
    member = next(
        m
        for m in auth.service.members(auth.cookie, credentials(auth)[1])["items"]
        if m["principal_id"] == str(auth.target)
    )
    assert member["version"] == "2"
    with pytest.raises(BusinessError):
        target_service.view(target_cookie)
    core = create_engine(os.environ["MED_CONTROL_DATABASE_URL"], hide_parameters=True)
    try:
        with pytest.raises(BusinessError):
            SessionStore(core).authenticate(target_cookie, uuid4(), "1")
    finally:
        core.dispose()
    with pytest.raises(BusinessError) as error:
        auth.service.change_member(
            *credentials(auth), auth.target, 1, ["VIEWER"], [auth.entity], True
        )
    assert error.value.code == "VERSION_CONFLICT"
    with pytest.raises(BusinessError):
        auth.service.change_member(*credentials(auth), auth.principal, 1, ["VIEWER"], [], False)
    with pytest.raises(BusinessError):
        auth.service.change_member(
            *credentials(auth), auth.target, 2, ["VIEWER"], [auth.entity2], True
        )
    with pytest.raises(BusinessError):
        auth.service.change_member(*credentials(auth), uuid4(), 1, ["VIEWER"], [], True)


def test_control_api_origin_csrf_cookie_and_logout(auth):
    with TestClient(create_app(auth.config, auth.provider)) as client:
        login = client.get("/auth/login", follow_redirects=False)
        assert login.status_code == 303
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=lax" in login.headers["set-cookie"]
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        response = client.get(
            "/auth/callback", params={"state": state, "code": "code"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "MED_SESSION=" in response.headers["set-cookie"]
        view = client.get("/auth/session").json()["data"]
        headers = {
            "Origin": auth.config.public_origin,
            "X-CSRF-Token": view["csrf_token"],
            "X-Context-Generation": view["context_generation"],
        }
        assert (
            client.post(
                "/auth/logout", headers={**headers, "Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        assert (
            client.post("/auth/logout", headers={**headers, "X-CSRF-Token": "wrong"}).status_code
            == 403
        )
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/auth/session").status_code == 401


def test_expired_state_cannot_create_session(auth):
    url, browser = auth.service.begin_login()
    state = parse_qs(urlsplit(url).query)["state"][0]
    auth.execute(
        """UPDATE control.login_attempt SET expires_at=clock_timestamp()-interval '1 second'
        WHERE state_hash=:h""",
        h=digest(state),
    )
    with pytest.raises(BusinessError) as error:
        auth.service.finish_login(state, browser, "code")
    assert error.value.code == "OIDC_STATE_INVALID"


def test_refresh_provider_failure_revokes_session(auth):
    creds = credentials(auth)
    expire_access(auth)

    def failure(tokens):
        raise RuntimeError("provider offline")

    auth.provider.refresh = failure
    with pytest.raises(BusinessError) as error:
        auth.service.refresh(*creds)
    assert error.value.code == "REAUTHENTICATE"
    with pytest.raises(BusinessError):
        auth.service.view(auth.cookie)


def test_viewer_cannot_manage_members(auth):
    target_service = AuthService(auth.engine, auth.config, FakeProvider(auth.target_subject))
    url, browser = target_service.begin_login()
    state = parse_qs(urlsplit(url).query)["state"][0]
    cookie = target_service.finish_login(state, browser, "code")
    view = target_service.view(cookie)
    with pytest.raises(BusinessError) as error:
        target_service.change_member(
            cookie,
            view["context_generation"],
            view["csrf_token"],
            auth.principal,
            1,
            ["VIEWER"],
            [auth.entity],
            False,
        )
    assert error.value.status == 403
    with pytest.raises(BusinessError):
        target_service.members(cookie, view["context_generation"])


@pytest.mark.parametrize("action", ["logout", "revoke", "switch"])
def test_business_guard_holds_authorization_changes_until_business_finishes(auth, action):
    cookie, generation, csrf = credentials(auth)
    core = create_engine(os.environ["MED_CONTROL_DATABASE_URL"], hide_parameters=True)
    store = SessionStore(core)
    started = Event()

    def change():
        started.set()
        if action == "logout":
            auth.service.logout(cookie, generation, csrf)
        elif action == "switch":
            auth.service.switch_tenant(cookie, generation, csrf, auth.second)
        else:
            auth.execute(
                """UPDATE control.membership SET active=false,
                permission_version=permission_version+1 WHERE tenant_id=:t AND principal_id=:p""",
                t=auth.tenant,
                p=auth.principal,
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with store.guard(cookie, uuid4(), generation, csrf) as ctx:
                assert ctx.principal_id == auth.principal
                future = pool.submit(change)
                assert started.wait(timeout=2)
                time.sleep(0.15)
                assert not future.done()
            future.result(timeout=3)
        with pytest.raises(BusinessError) as error, store.guard(cookie, uuid4(), generation, csrf):
            pytest.fail("revoked session entered a business command")
        assert error.value.status == (409 if action == "switch" else 401)
    finally:
        core.dispose()


def test_core_catalog_api_requires_enabled_commands_origin_csrf_and_permission(auth):
    from medinv.entrypoints.core_api import create_app as core_app
    from medinv.platform.config import Settings

    cookie, generation, csrf = credentials(auth)
    body = {
        "code": "P-API",
        "name": "测试",
        "category": "DEVICE",
        "manufacturer_name": "测试",
        "specification": "个",
        "approval_number": "TEST",
        "valid_from": "2026-01-01",
        "valid_until": "2030-01-01",
        "sku_code": "S-API",
        "base_unit": "个",
        "qty_scale": 0,
    }
    headers = {
        "Origin": auth.config.public_origin,
        "X-CSRF-Token": csrf,
        "X-Context-Generation": generation,
        "Idempotency-Key": str(uuid4()),
    }
    path = "/api/v1/catalog/products"
    with TestClient(core_app(Settings(procurement_commands_enabled=False))) as client:
        client.cookies.set("MED_SESSION", cookie)
        assert client.post(path, json=body, headers=headers).status_code == 503
    with TestClient(core_app(Settings(procurement_commands_enabled=True))) as client:
        client.cookies.set("MED_SESSION", cookie)
        for changes, code in [
            ({"Origin": "https://evil.invalid"}, "ORIGIN_REJECTED"),
            ({"X-CSRF-Token": "wrong"}, "CSRF_REJECTED"),
            ({"X-Context-Generation": "999"}, "CONTEXT_STALE"),
            ({}, "FORBIDDEN"),
        ]:
            response = client.post(path, json=body, headers={**headers, **changes})
            assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "role,allowed,denied",
    [
        ("PURCHASER", "purchase.write", "purchase.receive"),
        ("WAREHOUSE", "purchase.receive", "sales.write"),
        ("SALES", "sales.allocate", "sales.pick"),
        ("CASHIER", "retail.sell", "finance.write"),
    ],
)
def test_job_roles_reach_core_session_without_cross_job_writes(auth, role, allowed, denied):
    auth.service.change_member(*credentials(auth), auth.target, 1, [role], [auth.entity], True)
    service = AuthService(auth.engine, auth.config, FakeProvider(auth.target_subject))
    url, browser = service.begin_login()
    state = parse_qs(urlsplit(url).query)["state"][0]
    cookie = service.finish_login(state, browser, "code")
    current = service.view(cookie)
    assert current["role_codes"] == [role]
    core = create_engine(os.environ["MED_CONTROL_DATABASE_URL"], hide_parameters=True)
    try:
        ctx = SessionStore(core).authenticate(cookie, uuid4(), current["context_generation"])
        ctx.require(allowed, auth.entity)
        ctx.require("returns.read", auth.entity)
        ctx.require(
            {
                "PURCHASER": "returns.purchase.write",
                "SALES": "returns.sales.write",
                "CASHIER": "returns.retail.write",
                "WAREHOUSE": "returns.custody",
            }[role],
            auth.entity,
        )
        assert "returns.write" not in ctx.permissions
        for blocked in (
            denied,
            "members.manage",
            "catalog.review",
            "sales.credit",
            "retail.refund",
        ):
            with pytest.raises(BusinessError) as error:
                ctx.require(blocked, auth.entity)
            assert error.value.status == 403
        with pytest.raises(BusinessError):
            ctx.require(allowed, auth.entity2)
        auth.service.change_member(
            *credentials(auth), auth.target, 2, ["VIEWER"], [auth.entity], True
        )
        with pytest.raises(BusinessError) as error:
            SessionStore(core).authenticate(cookie, uuid4(), current["context_generation"])
        assert error.value.status == 401
    finally:
        core.dispose()


def test_combined_job_roles_can_be_removed_without_retaining_permissions(auth):
    auth.service.change_member(
        *credentials(auth), auth.target, 1, ["PURCHASER", "WAREHOUSE"], [auth.entity], True
    )
    with auth.owner.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        permissions = conn.execute(
            text(
                "SELECT permissions FROM control.membership WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": auth.tenant, "p": auth.target},
        ).scalar_one()
    assert set(permissions) == ROLE_PERMISSIONS["PURCHASER"] | ROLE_PERMISSIONS["WAREHOUSE"]
    auth.service.change_member(*credentials(auth), auth.target, 2, ["CASHIER"], [auth.entity], True)
    with auth.owner.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        permissions = conn.execute(
            text(
                "SELECT permissions FROM control.membership WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": auth.tenant, "p": auth.target},
        ).scalar_one()
    assert "retail.sell" in permissions
    assert not {"purchase.write", "purchase.receive", "sales.pick"}.intersection(permissions)
