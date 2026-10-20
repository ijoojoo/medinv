"""Local development commands. Does not print passwords or session tokens."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def environment():
    path = ROOT / "deploy/.env"
    if not path.exists():
        raise SystemExit("Create deploy/.env from .env.example first")
    secrets = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines()
                   if line and not line.startswith("#"))
    from urllib.parse import quote
    def url(role, key, database):
        return f"postgresql+psycopg://{role}:{quote(secrets[key], safe='')}@127.0.0.1:55432/{database}"
    env = os.environ.copy()
    env.update({
        "MED_ENVIRONMENT": "development",
        "MED_PROCUREMENT_COMMANDS_ENABLED": "true",
        "MED_SALES_COMMANDS_ENABLED": "true",
        "MED_SETTLEMENT_COMMANDS_ENABLED": "true",
        "MED_RETURNS_COMMANDS_ENABLED": "true",
        "MED_RETAIL_COMMANDS_ENABLED": "true",
        "MED_COUNT_COMMANDS_ENABLED": "true",
        "MED_QUALITY_COMMANDS_ENABLED": "true",
        "MED_TRANSFER_COMMANDS_ENABLED": "true",
        "MED_WAREHOUSE_COMMANDS_ENABLED": "true",
        "MED_OPENING_COMMANDS_ENABLED": "true",
        "MED_RESERVATION_EXPIRY_ENABLED": "true",
        "MED_PUBLIC_ORIGIN": "http://127.0.0.1:5173",
        "MED_OIDC_ISSUER_URL": "http://127.0.0.1:5173/identity/realms/medinv",
        "MED_OIDC_CLIENT_SECRET": secrets["OIDC_CLIENT_SECRET"],
        "MED_SESSION_ENCRYPTION_KEY": secrets["SESSION_ENCRYPTION_KEY"],
        "MED_CONTROL_AUTH_DATABASE_URL": url("control_auth", "CONTROL_AUTH_PASSWORD", "med_control"),
        "MED_DATABASE_URL": url("med_runtime", "MED_RUNTIME_PASSWORD", "med_business"),
        "MED_CONTROL_DATABASE_URL": url("control_runtime", "CONTROL_RUNTIME_PASSWORD", "med_control"),
        "MED_MIGRATION_URL": url("med_migrator", "MED_MIGRATOR_PASSWORD", "med_business"),
        "MED_CONTROL_MIGRATION_URL": url("control_migrator", "CONTROL_MIGRATOR_PASSWORD", "med_control"),
        "MED_TEST_DATABASE_URL": url("med_runtime", "MED_RUNTIME_PASSWORD", "med_business"),
    })
    return env


def run(*args):
    subprocess.run([str(PYTHON), *args], cwd=BACKEND, env=environment(), check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "test", "check", "serve", "auth", "expiry"])
    parser.add_argument("extra", nargs="*")
    parser.add_argument("--once", action="store_true", help="Run one bounded expiry scan")
    args = parser.parse_args()
    if args.command == "migrate":
        for chain in ("business", "control"):
            run("-m", "alembic", "-c", f"alembic-{chain}.ini", "upgrade", "head")
    elif args.command == "check":
        run("-m", "ruff", "check", ".")
        run("-m", "ruff", "format", "--check", ".")
        run("-m", "mypy")
        run("-m", "pytest", *args.extra)
    elif args.command == "test":
        run("-m", "pytest", *args.extra)
    elif args.command == "expiry":
        run("-m", "medinv.entrypoints.expiry_worker", *args.extra,
            *(["--once"] if args.once else []))
    elif args.command == "auth":
        run("-m", "uvicorn", "medinv.entrypoints.control_api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", "8001", "--no-access-log")
    else:
        run("-m", "uvicorn", "medinv.entrypoints.core_api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", "8000")
