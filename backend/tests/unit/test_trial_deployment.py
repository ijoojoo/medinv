"""Deployment command boundaries without touching Docker, credentials or real data."""

import base64
import importlib.util
import json
from pathlib import Path
from secrets import token_bytes, token_urlsafe

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/trial.py"
SPEC = importlib.util.spec_from_file_location("trial_deployment", SCRIPT)
assert SPEC and SPEC.loader
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(trial, "CACHE", tmp_path / ".cache/trial")
    (tmp_path / "deploy/trial").mkdir(parents=True)
    original = SCRIPT.parent.parent / "deploy/trial/compatibility.json"
    (tmp_path / "deploy/trial/compatibility.json").write_bytes(original.read_bytes())
    values = {key: token_urlsafe(24) for key in trial.SECRET_KEYS}
    values["SESSION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(token_bytes(32)).decode()
    (tmp_path / "deploy/.env").write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8"
    )
    return tmp_path, values


@pytest.mark.parametrize("mistake", ["missing", "placeholder", "duplicate", "reused", "key"])
def test_invalid_config_fails_without_exposing_values(workspace, mistake):
    root, values = workspace
    path = root / "deploy/.env"
    content = path.read_text(encoding="utf-8")
    if mistake == "missing":
        content = content.replace(f"OIDC_CLIENT_SECRET={values['OIDC_CLIENT_SECRET']}\n", "")
    elif mistake == "placeholder":
        content = content.replace(
            values["POSTGRES_PASSWORD"], "replace-with-a-distinct-random-secret"
        )
    elif mistake == "duplicate":
        content += f"POSTGRES_PASSWORD={values['POSTGRES_PASSWORD']}\n"
    elif mistake == "reused":
        content = content.replace(values["MED_RUNTIME_PASSWORD"], values["POSTGRES_PASSWORD"])
    else:
        content = content.replace(values["SESSION_ENCRYPTION_KEY"], "not-a-key")
    path.write_text(content, encoding="utf-8")
    with pytest.raises(trial.TrialError) as error:
        trial.read_secrets()
    assert all(v not in str(error.value) for v in values.values())


def test_runtime_files_exclude_admin_migration_keys_and_preserve_original_config(workspace):
    root, values = workspace
    before = (root / "deploy/.env").read_bytes()
    trial.write_runtime()
    trial.write_runtime()
    assert (root / "deploy/.env").read_bytes() == before
    core = (root / ".cache/trial/core.env").read_text(encoding="utf-8")
    auth = (root / ".cache/trial/auth.env").read_text(encoding="utf-8")
    for key in (
        "POSTGRES_PASSWORD",
        "MED_MIGRATOR_PASSWORD",
        "CONTROL_MIGRATOR_PASSWORD",
        "KEYCLOAK_ADMIN_PASSWORD",
        "DEMO_USER_PASSWORD",
    ):
        assert values[key] not in core + auth
    assert values["CONTROL_AUTH_PASSWORD"] not in core
    assert values["MED_RUNTIME_PASSWORD"] not in auth
    assert "MED_INVENTORY_COMMANDS_ENABLED" not in core
    assert "MED_RESERVATION_EXPIRY_ENABLED=true" in core


def test_source_or_image_drift_prevents_start(workspace, monkeypatch):
    root, _ = workspace
    source = root / "example.py"
    source.write_text("original", encoding="utf-8")
    release = {
        "compatibility": trial.compatibility(),
        "files": {"example.py": trial.digest(source)},
        "images": {"core": {"tag": "medinv-core:0.24.0", "id": "sha256:expected"}},
    }
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    monkeypatch.setattr(trial, "run", lambda *args, **kwargs: "sha256:expected\n")
    assert trial.verify_release() == release
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(trial.TrialError, match="已变化"):
        trial.verify_release()
    source.write_text("original", encoding="utf-8")
    monkeypatch.setattr(trial, "run", lambda *args, **kwargs: "sha256:other\n")
    with pytest.raises(trial.TrialError, match="镜像与构建清单不符"):
        trial.verify_release()


def test_rebuilding_an_unpacked_delivery_replaces_its_active_manifest(workspace, monkeypatch):
    root, _ = workspace
    source = root / "example.py"
    source.write_text("updated source", encoding="utf-8")
    (root / "release.json").write_text('{"old":true}', encoding="utf-8")
    monkeypatch.setattr(trial.shutil, "which", lambda _: "pnpm")
    monkeypatch.setattr(trial, "source_files", lambda: [source])
    monkeypatch.setattr(trial, "run", lambda *args, **kwargs: "sha256:new\n")
    trial.build()
    release = trial.verify_release()
    assert release["files"] == {"example.py": trial.digest(source)}
    assert release["images"]["core"]["id"] == "sha256:new"
    assert not (root / ".cache/trial/release.json").exists()


def test_schema_mismatch_never_starts_application_containers(workspace, monkeypatch):
    commands = []
    monkeypatch.setattr(trial.sys, "argv", ["trial.py", "start"])
    monkeypatch.setattr(trial, "verify_release", lambda: {})
    monkeypatch.setattr(trial, "rows", lambda: {})
    monkeypatch.setattr(trial, "check_ports", lambda _: None)
    monkeypatch.setattr(trial, "run", lambda label, args, **kw: commands.append(args) or "")

    def mismatch():
        raise trial.TrialError("数据库修订不一致")

    monkeypatch.setattr(trial, "check_schema", mismatch)
    with pytest.raises(trial.TrialError, match="修订"):
        trial.main()
    starts = [cmd for cmd in commands if "up" in cmd]
    assert len(starts) == 1 and starts[0][-1] == "postgres"
    assert all("alembic" not in cmd and "down" not in cmd for cmd in commands)


def test_stop_preserves_volumes_and_does_not_depend_on_current_build(workspace, monkeypatch):
    trial.write_runtime()
    commands = []
    monkeypatch.setattr(trial.sys, "argv", ["trial.py", "stop"])
    monkeypatch.setattr(trial, "run", lambda label, args, **kw: commands.append(args) or "")
    trial.main()
    assert "stop" in commands[-1]
    assert all("down" not in cmd and "--volumes" not in cmd for cmd in commands)
    assert all(name in commands[-1] for name in trial.SERVICES)


def test_busy_port_is_reported_without_terminating_external_process(workspace, monkeypatch):
    class Occupied:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

        def connect_ex(self, address):
            return 0

    monkeypatch.setattr(trial.socket, "socket", Occupied)
    with pytest.raises(trial.TrialError, match="5173端口"):
        trial.check_ports({})
