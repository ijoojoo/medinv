"""Create missing local secrets, preserving existing database and identity credentials."""
import base64
from pathlib import Path
from secrets import token_bytes, token_urlsafe

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "deploy/.env"
values = {}
if path.exists():
    values = dict(line.split("=",1) for line in path.read_text(encoding="utf-8-sig").splitlines()
                  if line and not line.startswith("#"))
for key in ["POSTGRES_PASSWORD", "MED_RUNTIME_PASSWORD", "MED_MIGRATOR_PASSWORD",
            "CONTROL_RUNTIME_PASSWORD", "CONTROL_MIGRATOR_PASSWORD", "CONTROL_AUTH_PASSWORD",
            "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "OIDC_CLIENT_SECRET", "DEMO_USER_PASSWORD"]:
    values.setdefault(key,token_urlsafe(24))
values.setdefault("SESSION_ENCRYPTION_KEY",base64.urlsafe_b64encode(token_bytes(32)).decode())
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text("".join(f"{key}={value}\n" for key,value in values.items()),encoding="utf-8")
print("Local environment ready; existing secrets preserved.")
