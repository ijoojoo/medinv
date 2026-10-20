"""Apply local login branding and sync demo identities without resetting passwords."""

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from dev import environment
from dev_accounts import ACCOUNTS
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(
        line.split("=", 1)
        for line in (ROOT / "deploy/.env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    realm = json.loads(
        (ROOT / ".cache/keycloak/medinv-realm.json").read_text(encoding="utf-8")
    )
    os.environ.update(environment())
    engine = create_engine(
        os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True
    )
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8080/identity/",
            timeout=10,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": "local-admin",
                    "password": env["KEYCLOAK_ADMIN_PASSWORD"],
                },
            )
            response.raise_for_status()
            client.headers["Authorization"] = (
                "Bearer " + response.json()["access_token"]
            )
            response = client.put(
                "admin/realms/medinv",
                json={
                    key: realm[key]
                    for key in (
                        "displayName",
                        "loginTheme",
                        "internationalizationEnabled",
                        "supportedLocales",
                        "defaultLocale",
                    )
                },
            )
            response.raise_for_status()
            identities = []
            allowed = {
                name: str(uuid5(NAMESPACE_URL, "medinv-dev/" + name))
                for name, _ in ACCOUNTS
            }
            for user in realm["users"]:
                if allowed.get(user["username"]) != user["id"]:
                    raise RuntimeError(
                        "Unexpected local demo identity; no mapping allowed"
                    )

                def lookup(username=user["username"]):
                    result = client.get(
                        "admin/realms/medinv/users",
                        params={"username": username, "exact": "true"},
                    )
                    result.raise_for_status()
                    return result.json()

                matches = lookup()
                if not matches:
                    result = client.post("admin/realms/medinv/users", json=user)
                    result.raise_for_status()
                    matches = lookup()
                if len(matches) != 1:
                    raise RuntimeError("Local demo identity is ambiguous")
                subject = matches[0]["id"]
                identities.append({"subject": subject, "id": user["id"]})
        # Only the fixed seeded development accounts may move from the known
        # loopback issuer. Verify the existing mapping before making any changes.
        issuer = os.environ["MED_OIDC_ISSUER_URL"]
        legacy_issuer = "http://127.0.0.1:8080/realms/medinv"
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE control_owner"))
            for identity in identities:
                old = (
                    conn.execute(
                        text("""SELECT issuer,subject FROM control.principal
                    WHERE id=:id FOR UPDATE"""),
                        identity,
                    )
                    .mappings()
                    .one()
                )
                if old["issuer"] not in (legacy_issuer, issuer) or old[
                    "subject"
                ] not in (identity["id"], identity["subject"]):
                    raise RuntimeError(
                        "Unexpected local identity mapping; no mappings changed"
                    )
                if old["issuer"] == issuer and old["subject"] == identity["subject"]:
                    continue
                conn.execute(
                    text("""UPDATE control.web_session
                    SET revoked_at=clock_timestamp(),sealed_tokens=NULL
                    WHERE principal_id=:id AND revoked_at IS NULL"""),
                    identity,
                )
                conn.execute(
                    text("""UPDATE control.principal SET issuer=:issuer,subject=:subject
                    WHERE id=:id"""),
                    {**identity, "issuer": issuer},
                )
        print(
            "Local login theme and demo identities synchronized; existing passwords preserved."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
