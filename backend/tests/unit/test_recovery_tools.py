"""Backup/restore boundaries; never connects to Docker or a real database."""

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("recovery_tools", SCRIPTS / "recovery.py")
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "ROOT", tmp_path)
    monkeypatch.setattr(recovery, "CACHE", tmp_path / ".cache/recovery")
    monkeypatch.setattr(recovery, "JOURNAL", tmp_path / ".cache/recovery/source-maintenance.json")
    monkeypatch.setattr(recovery.trial, "compatibility", lambda: {"version": "test"})
    monkeypatch.setattr(recovery.trial, "compose", lambda *args: ["docker", "compose", *args])
    return tmp_path


def archive(root, mutation=None):
    key = Fernet.generate_key()
    payload = {
        "roles.sql": b"CREATE ROLE sample;",
        "private.env": b"a=private-test-value",
        "stack.json": b"{}",
        **{d + ".dump": b"dump" for d in recovery.DATABASES},
    }
    manifest = {
        "format": 1,
        "compatibility": {"version": "test"},
        "sha256": {k: recovery.digest(v) for k, v in payload.items()},
    }
    if mutation:
        mutation(payload, manifest)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, data in payload.items():
            bundle.writestr(name, data)
        bundle.writestr("manifest.json", json.dumps(manifest))
    path, key_path = root / "backup.medbackup", root / "restore.key"
    path.write_bytes(Fernet(key).encrypt(stream.getvalue()))
    key_path.write_bytes(key)
    return path, key_path


def test_encrypted_roundtrip_preserves_all_three_databases(workspace):
    path, key = archive(workspace)
    assert b"private-test-value" not in path.read_bytes()
    _, payload = recovery.unpack(path, key)
    assert payload["private.env"] == b"a=private-test-value"
    assert all(d + ".dump" in payload for d in recovery.DATABASES)


@pytest.mark.parametrize("mistake", ["key", "ciphertext", "checksum", "path", "missing", "version"])
def test_invalid_backup_is_rejected_before_any_docker_call(workspace, monkeypatch, mistake):
    def mutate(payload, manifest):
        if mistake == "checksum":
            payload["roles.sql"] += b"unexpected"
        elif mistake == "path":
            payload["../outside"] = b"bad"
        elif mistake == "missing":
            payload.pop("med_identity.dump")
            manifest["sha256"].pop("med_identity.dump")
        elif mistake == "version":
            manifest["format"] = 9

    path, key = archive(workspace, mutate)
    if mistake == "key":
        key.write_bytes(Fernet.generate_key())
    elif mistake == "ciphertext":
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(data)
    calls = []
    monkeypatch.setattr(recovery, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(recovery.trial.TrialError):
        recovery.restore(path, key)
    assert calls == []


@pytest.mark.parametrize("target", ["medinv-dev", "../medinv-dev", "medinv-restore-../../x"])
def test_source_and_external_targets_cannot_be_stopped(workspace, monkeypatch, target):
    calls = []
    monkeypatch.setattr(recovery, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(recovery.trial.TrialError):
        recovery.stop(target)
    assert calls == []


def test_existing_restore_volume_is_never_reused(workspace, monkeypatch):
    manifest = {"images": {s: "sha256:" + "a" * 64 for s in recovery.trial.SERVICES}}
    monkeypatch.setattr(recovery, "unpack", lambda *args: (manifest, {}))
    monkeypatch.setattr(recovery, "free_port", lambda _: None)
    monkeypatch.setattr(recovery, "uuid4", lambda: SimpleNamespace(hex="1" * 32))
    monkeypatch.setattr(recovery, "containers", lambda _: {})
    calls = []

    def command(label, args):
        calls.append(args)
        return b"existing-volume" if "volume" in args else b"[]"

    monkeypatch.setattr(recovery, "run", command)
    with pytest.raises(recovery.trial.TrialError, match="拒绝覆盖"):
        recovery.restore(Path("x"), Path("y"))
    assert all("up" not in args and "pg_restore" not in args for args in calls)
    assert not recovery.target_path("medinv-restore-" + "1" * 12).exists()


def test_pause_failure_unfences_and_resumes_original_services(workspace, monkeypatch):
    original = {
        "postgres": {"Id": "source-pg", "State": {"Running": True}},
        **{s: {"State": {"Running": True}} for s in recovery.WRITERS},
    }
    monkeypatch.setattr(recovery, "source", lambda: original)
    monkeypatch.setattr(recovery, "limits", lambda _: {d: -1 for d in recovery.DATABASES})
    operations = []
    monkeypatch.setattr(recovery, "set_limits", lambda pg, values: operations.append(values))
    monkeypatch.setattr(recovery, "run", lambda label, args: operations.append(args) or b"")

    def busy(_):
        raise recovery.trial.TrialError("other writer")

    monkeypatch.setattr(recovery, "no_clients", busy)
    with pytest.raises(recovery.trial.TrialError, match="other writer"):
        recovery.pause_source()
    assert {d: 0 for d in recovery.DATABASES} in operations
    assert {d: -1 for d in recovery.DATABASES} in operations
    assert any(isinstance(op, list) and "up" in op for op in operations)
    assert not recovery.JOURNAL.exists()


def test_resume_never_reclaims_a_running_drill_gateway(workspace, monkeypatch):
    recovery.private_write(recovery.JOURNAL, b"{}")
    monkeypatch.setattr(recovery, "run", lambda *args: b"medinv-restore-aaaaaaaaaaaa-web-1\n")
    with pytest.raises(recovery.trial.TrialError, match="入口仍运行"):
        recovery.resume_source()
    assert recovery.JOURNAL.exists()


def test_restored_stack_has_no_source_volume_network_or_import(workspace):
    config = {
        "services": {
            s: {
                "image": "old",
                "restart": "unless-stopped",
                "networks": {"source": None},
                "environment": {},
                "volumes": ["source-data:/var/lib/postgresql"],
                "ports": ["127.0.0.1:9999:9999"],
            }
            for s in recovery.trial.SERVICES
        }
    }
    config["services"]["postgres"].update(
        environment={"POSTGRES_PASSWORD": "original"}, healthcheck={}
    )
    config["services"]["keycloak"]["environment"]["KC_BOOTSTRAP_ADMIN_PASSWORD"] = "admin"
    for name in ("core", "auth", "expiry"):
        config["services"][name]["network_mode"] = "service:web"
    manifest = {"images": {s: "sha256:" + "a" * 64 for s in recovery.trial.SERVICES}}
    result = recovery.restored_config(config, manifest, "medinv-restore-" + "a" * 12, workspace)
    assert result["volumes"] == {"pg": {}}
    assert "networks" not in result
    assert result["services"]["postgres"]["volumes"] == ["pg:/var/lib/postgresql"]
    assert result["services"]["keycloak"]["command"] == ["start-dev"]
    assert "KC_BOOTSTRAP_ADMIN_PASSWORD" not in result["services"]["keycloak"]["environment"]
    for name, settings in result["services"].items():
        assert settings["restart"] == "no"
        assert "networks" not in settings
        if name in ("core", "auth", "expiry"):
            assert "ports" not in settings
            assert settings["network_mode"] == "service:web"
