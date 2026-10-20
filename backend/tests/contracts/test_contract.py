import json
from pathlib import Path

from pydantic import SecretStr

from medinv.entrypoints.core_api import create_app
from medinv.platform.config import Settings


def test_runtime_contract_is_identical_to_reviewed_contract():
    root = Path(__file__).parents[3]
    canonical = json.loads(
        (root / "contracts/openapi/core-openapi.json").read_text(encoding="utf-8-sig")
    )
    packaged = json.loads(
        (root / "backend/src/medinv/contracts/core-openapi.json").read_text(encoding="utf-8")
    )
    assert packaged == canonical


def test_published_api_snapshot_matches_current_routes_and_schemas():
    """Keep the delivered API snapshot in sync without starting a database."""
    root = Path(__file__).parents[3]
    snapshot = json.loads(
        (root / "contracts/openapi/implemented-core-openapi.json").read_text(encoding="utf-8")
    )
    unused_url = SecretStr("postgresql+psycopg://contract:contract@127.0.0.1:1/contract")
    app = create_app(Settings(database_url=unused_url, control_database_url=unused_url))
    assert app.openapi() == snapshot
