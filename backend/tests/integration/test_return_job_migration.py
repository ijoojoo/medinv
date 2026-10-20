import importlib.util
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text
from test_auth import FakeProvider
from test_auth import auth as auth

from medinv.platform.auth.service import AuthService

pytestmark = pytest.mark.integration


def test_return_job_upgrade_preserves_roles_scope_and_disabled_members_and_revokes_old_sessions(
    auth, monkeypatch
):
    with auth.owner.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET role_codes=ARRAY['PURCHASER','WAREHOUSE'],
          permissions=ARRAY['inventory.read','purchase.write','custom.read']
          WHERE tenant_id=:t AND principal_id=:p"""),
            {"t": auth.tenant, "p": auth.target},
        )
    service = AuthService(auth.engine, auth.config, FakeProvider(auth.target_subject))
    url, browser = service.begin_login()
    cookie = service.finish_login(parse_qs(urlsplit(url).query)["state"][0], browser, "code")
    assert service.view(cookie)["permission_version"] == "1"
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/control/versions/0016_return_job_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("return_job_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with auth.owner.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text("SET LOCAL ROLE control_owner"))
            conn.execute(
                text("""UPDATE control.membership SET role_codes=ARRAY['WAREHOUSE'],active=false
              WHERE tenant_id=:t AND principal_id=:p"""),
                {"t": auth.second, "p": auth.principal},
            )
            monkeypatch.setattr(
                migration, "op", SimpleNamespace(execute=lambda sql: conn.execute(text(sql)))
            )
            migration.upgrade()

            def member(tenant, principal):
                return (
                    conn.execute(
                        text(
                            "SELECT * FROM control.membership "
                            "WHERE tenant_id=:t AND principal_id=:p"
                        ),
                        {"t": tenant, "p": principal},
                    )
                    .mappings()
                    .one()
                )

            updated = member(auth.tenant, auth.target)
            assert updated["permission_version"] == 2
            assert updated["role_codes"] == ["PURCHASER", "WAREHOUSE"]
            assert updated["entity_ids"] == [auth.entity]
            assert set(updated["permissions"]) == {
                "inventory.read",
                "purchase.write",
                "custom.read",
                "returns.read",
                "returns.purchase.write",
                "returns.custody",
            }
            assert updated["active"]
            assert not member(auth.second, auth.principal)["active"]
            assert member(auth.tenant, auth.principal)["permission_version"] == 1
            sessions = (
                conn.execute(
                    text("""SELECT revoked_at IS NOT NULL AND sealed_tokens IS NULL
              AND refresh_id IS NULL AND refresh_until IS NULL FROM control.web_session
              WHERE tenant_id=:t AND principal_id=:p"""),
                    {"t": auth.tenant, "p": auth.target},
                )
                .scalars()
                .all()
            )
            assert sessions and all(sessions)
            migration.upgrade()
            assert member(auth.tenant, auth.target)["permission_version"] == 2
            assert member(auth.second, auth.principal)["permission_version"] == 2
        finally:
            # Do not alter unrelated live memberships when replaying the upgrade SQL.
            transaction.rollback()
