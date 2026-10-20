"""Encrypted local backup and restore drills into a NEW isolated Docker volume.

Uses the backend environment (cryptography). Never restores over the source.
The canonical login URL is temporarily handed to the drill, then handed back.
"""

import argparse
import copy
import io
import json
import re
import socket
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import trial
from cryptography.fernet import Fernet, InvalidToken

ROOT = trial.ROOT
CACHE = ROOT / ".cache/recovery"
JOURNAL = CACHE / "source-maintenance.json"
DATABASES = {"med_business": "med", "med_control": "control", "med_identity": "public"}
WRITERS = ("web", "expiry", "core", "auth", "keycloak")
MAX_BYTES = 256 * 1024 * 1024


def run(label, command, data=None, timeout=600):
    try:
        result = subprocess.run(
            list(map(str, command)),
            input=data,
            capture_output=True,
            cwd=ROOT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise trial.TrialError(f"{label}未完成；保留现场，核对Docker状态。") from exc
    if result.returncode:
        # Database exports and SQL errors can contain passwords/data. Never log them.
        raise trial.TrialError(f"{label}失败，原始输出未展示；恢复不会覆盖原库。")
    return result.stdout


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o600)


def sql(container, database, query):
    return (
        run(
            "数据库检查",
            [
                "docker",
                "exec",
                "-i",
                container,
                "psql",
                "-X",
                "-U",
                "postgres",
                "-d",
                database,
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            query.encode(),
        )
        .decode()
        .strip()
    )


def containers(project):
    ids = (
        run(
            "读取项目容器",
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        )
        .decode()
        .split()
    )
    if not ids:
        return {}
    items = json.loads(run("核对项目容器", ["docker", "inspect", *ids]))
    result = {}
    for item in items:
        service = item["Config"]["Labels"]["com.docker.compose.service"]
        if service in result:
            raise trial.TrialError("项目存在重复服务容器，先人工核对。")
        result[service] = item
    return result


def source():
    state = containers(trial.compatibility()["project"])
    if "postgres" not in state or not state["postgres"]["State"]["Running"]:
        raise trial.TrialError("原数据库未运行，不能开始维护。")
    return state


def limits(pg):
    lines = sql(
        pg,
        "postgres",
        "SELECT datname||'|'||datconnlimit FROM pg_database "
        "WHERE datname IN ('med_business','med_control','med_identity') ORDER BY datname;",
    )
    result = {
        name: int(value)
        for name, value in (line.split("|") for line in lines.splitlines())
    }
    if set(result) != set(DATABASES):
        raise trial.TrialError("三个业务相关数据库不齐全。")
    return result


def set_limits(pg, values):
    if set(values) != set(DATABASES):
        raise trial.TrialError("维护记录数据库范围不正确。")
    for name, value in values.items():
        if type(value) is not int or value < -1:
            raise trial.TrialError("维护记录连接限制不正确。")
        sql(pg, "postgres", f"ALTER DATABASE {name} CONNECTION LIMIT {value};")


def no_clients(pg):
    count = sql(
        pg,
        "postgres",
        "SELECT count(*) FROM pg_stat_activity WHERE "
        "datname IN ('med_business','med_control','med_identity') "
        "AND backend_type='client backend';",
    )
    if count != "0":
        raise trial.TrialError("仍有外部数据库连接；请停止其他写入工具后重试备份。")


def disconnect_stopped_idle_clients(pg, before):
    """Release orphaned idle sockets from this stopped Compose stack only."""
    from ipaddress import ip_address

    after = source()
    if any(after.get(name, {}).get("State", {}).get("Running") for name in WRITERS):
        raise trial.TrialError("本项目写入服务尚未全部停止，不能回收遗留连接。")
    addresses = {
        str(ip_address(network["IPAddress"]))
        for name, item in before.items()
        if name in WRITERS
        for network in item.get("NetworkSettings", {}).get("Networks", {}).values()
        if network.get("IPAddress")
    }
    if not addresses:
        return
    literals = ",".join("'" + address + "'::inet" for address in sorted(addresses))
    sql(
        pg,
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
        "datname IN ('med_business','med_control','med_identity') "
        "AND usename IN ('med_runtime','control_runtime','control_auth','keycloak') "
        f"AND client_addr IN ({literals}) "
        "AND backend_type='client backend' AND state='idle' AND xact_start IS NULL;",
    )


def pause_source():
    state = source()
    active = [s for s in WRITERS if s in state and state[s]["State"]["Running"]]
    pg = state["postgres"]["Id"]
    journal = {
        "source_pg": pg,
        "limits": limits(pg),
        "active": active,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if JOURNAL.exists():
        raise trial.TrialError("存在未结束的维护记录；先执行 resume-source。")
    private_write(JOURNAL, json.dumps(journal).encode())
    try:
        if active:
            run("暂停原环境写入", trial.compose("stop", "--timeout", "30", *active))
        # Non-superuser writers cannot reconnect while the three dumps are taken.
        set_limits(pg, {name: 0 for name in DATABASES})
        disconnect_stopped_idle_clients(pg, state)
        no_clients(pg)
    except BaseException:
        resume_source()
        raise
    return journal


def resume_source():
    if not JOURNAL.exists():
        return
    # Never reclaim the canonical URL while a restored gateway is still running.
    running = run("检查演练入口", ["docker", "ps", "--format", "{{.Names}}"])
    if re.search(rb"medinv-restore-[a-f0-9]{12}-web-", running):
        raise trial.TrialError(
            "恢复副本入口仍运行；先执行 stop --target，再恢复原环境。"
        )
    journal = json.loads(JOURNAL.read_bytes())
    pg = source()["postgres"]["Id"]
    if pg != journal["source_pg"]:
        raise trial.TrialError("原数据库容器已改变，拒绝应用旧维护记录。")
    set_limits(pg, journal["limits"])
    if journal["active"]:
        if not set(journal["active"]).issubset(WRITERS):
            raise trial.TrialError("维护记录服务范围不正确。")
        run(
            "恢复原环境",
            trial.compose(
                "up", "-d", "--wait", "--wait-timeout", "120", *journal["active"]
            ),
        )
    JOURNAL.unlink()


def table_counts(pg):
    result = {}
    for database, schema in DATABASES.items():
        query = (
            "SELECT format('SELECT %L || ''|'' || count(*)::text FROM %I.%I;', "
            "n.nspname||'.'||c.relname,n.nspname,c.relname) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            f"WHERE n.nspname='{schema}' AND c.relkind IN ('r','p') ORDER BY c.relname\n\\gexec\n"
        )
        output = sql(pg, database, query)
        result[database] = {
            k: int(v) for k, v in (line.split("|") for line in output.splitlines())
        }
    return result


def backup():
    trial.verify_release()
    trial.read_secrets()
    state = source()
    if any(
        not state.get(s, {}).get("State", {}).get("Running") for s in trial.SERVICES
    ):
        raise trial.TrialError("先启动并核对六个原服务，再进行完整备份。")
    config = json.loads(
        run("读取运行配置", trial.compose("config", "--format", "json"))
    )
    identity = uuid4().hex
    print("暂停写入并备份业务、权限和身份数据…", flush=True)
    journal = pause_source()
    try:
        pg = journal["source_pg"]
        payload = {
            "stack.json": json.dumps(config).encode(),
            "private.env": (ROOT / "deploy/.env").read_bytes(),
        }
        payload["roles.sql"] = run(
            "备份数据库角色",
            ["docker", "exec", pg, "pg_dumpall", "-U", "postgres", "--roles-only"],
        )
        for name in DATABASES:
            no_clients(pg)
            payload[name + ".dump"] = run(
                "备份" + name,
                ["docker", "exec", pg, "pg_dump", "-U", "postgres", "-Fc", "-C", name],
            )
        no_clients(pg)
        theme = ROOT / "deploy/keycloak/themes/medinv"
        for path in theme.rglob("*"):
            if path.is_symlink():
                raise trial.TrialError("主题文件不可包含符号链接。")
            if path.is_file():
                payload["theme/" + path.relative_to(theme).as_posix()] = (
                    path.read_bytes()
                )
        manifest = {
            "format": 1,
            "backup_id": identity,
            "created_at": datetime.now(UTC).isoformat(),
            "compatibility": trial.compatibility(),
            "limits": journal["limits"],
            "source_system_identifier": sql(
                pg, "postgres", "SELECT system_identifier FROM pg_control_system();"
            ),
            "images": {s: state[s]["Image"] for s in trial.SERVICES},
            "table_counts": table_counts(pg),
            "sha256": {name: digest(data) for name, data in payload.items()},
        }
        no_clients(pg)
        payload["manifest.json"] = json.dumps(manifest).encode()
        if sum(map(len, payload.values())) > MAX_BYTES:
            raise trial.TrialError("备份超过本机工具256MiB范围，请使用专用备份设施。")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
        if stream.tell() > MAX_BYTES:
            raise trial.TrialError("备份超过本机工具256MiB范围，请使用专用备份设施。")
    finally:
        resume_source()
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(stream.getvalue())
    key_path = ROOT / ".cache/recovery-keys" / (identity + ".key")
    path = ROOT / ".cache/backups" / (identity + ".medbackup")
    private_write(key_path, key)
    private_write(path, encrypted)
    print(
        json.dumps(
            {
                "backup_id": identity,
                "backup": str(path),
                "key": str(key_path),
                "sha256": digest(encrypted),
                "bytes": len(encrypted),
            },
            ensure_ascii=False,
        )
    )


def digest(data):
    import hashlib

    return hashlib.sha256(data).hexdigest()


def unpack(path, key_path):
    if path.stat().st_size > MAX_BYTES * 2:
        raise trial.TrialError("备份超过本工具范围。")
    try:
        plain = Fernet(key_path.read_bytes()).decrypt(path.read_bytes())
    except (InvalidToken, ValueError) as exc:
        raise trial.TrialError("备份认证失败：文件损坏或恢复密钥不匹配。") from exc
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
            raise trial.TrialError("解密后备份超过本工具范围。")
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-Za-z0-9_./-]+", n)
            or n.startswith("/")
            or ".." in n.split("/")
            for n in names
        ):
            raise trial.TrialError("备份条目路径不正确。")
        payload = {name: archive.read(name) for name in names}
    manifest = json.loads(payload.pop("manifest.json"))
    if manifest["format"] != 1 or manifest["compatibility"] != trial.compatibility():
        raise trial.TrialError("备份版本与当前工具不兼容。")
    if {name: digest(data) for name, data in payload.items()} != manifest["sha256"]:
        raise trial.TrialError("备份条目校验失败。")
    required = {
        "stack.json",
        "private.env",
        "roles.sql",
        *(name + ".dump" for name in DATABASES),
    }
    if not required.issubset(payload):
        raise trial.TrialError("备份缺少数据库或配置。")
    return manifest, payload


def target_path(target):
    if not re.fullmatch(r"medinv-restore-[a-f0-9]{12}", target):
        raise trial.TrialError("恢复目标只能是工具创建的独立演练项目。")
    return CACHE / target


def target_compose(target, *args):
    return [
        "docker",
        "compose",
        "-p",
        target,
        "-f",
        target_path(target) / "compose.json",
        *args,
    ]


def free_port(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise trial.TrialError(f"演练端口{port}被占用，请先核对已有环境。")


def restored_config(config, manifest, target, folder):
    services = copy.deepcopy(config["services"])
    if set(services) != set(trial.SERVICES):
        raise trial.TrialError("备份服务范围不符合当前试用部署。")
    for name, settings in services.items():
        settings["image"] = manifest["images"][name]
        settings.pop("networks", None)
        settings.pop("env_file", None)
        settings.pop("ports", None)
        settings["restart"] = "no"
        settings.pop("volumes", None)
    services["postgres"]["volumes"] = ["pg:/var/lib/postgresql"]
    services["postgres"]["ports"] = ["127.0.0.1:55433:5432"]
    services["postgres"]["environment"] = {
        "POSTGRES_PASSWORD": services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    }
    services["postgres"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        "pg_isready -U postgres -d postgres",
    ]
    services["keycloak"]["command"] = ["start-dev"]
    services["keycloak"]["environment"].pop("KC_BOOTSTRAP_ADMIN_USERNAME", None)
    services["keycloak"]["environment"].pop("KC_BOOTSTRAP_ADMIN_PASSWORD", None)
    services["keycloak"]["volumes"] = [
        {
            "type": "bind",
            "source": str(folder / "theme"),
            "target": "/opt/keycloak/themes/medinv",
            "read_only": True,
        }
    ]
    services["keycloak"]["ports"] = ["127.0.0.1:8081:8080"]
    services["web"]["ports"] = ["127.0.0.1:5173:5173"]
    return {"name": target, "services": services, "volumes": {"pg": {}}}


def restore(path, key_path):
    manifest, payload = unpack(path, key_path)
    for image in set(manifest["images"].values()):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise trial.TrialError("备份镜像标识不正确。")
        run("核对原版本镜像", ["docker", "image", "inspect", image])
    free_port(55433)
    free_port(8081)
    target = "medinv-restore-" + uuid4().hex[:12]
    folder = target_path(target)
    if (
        folder.exists()
        or containers(target)
        or run(
            "检查新数据卷",
            ["docker", "volume", "ls", "-q", "--filter", f"name=^{target}_pg$"],
        ).strip()
    ):
        raise trial.TrialError("恢复目标已存在，拒绝覆盖。")
    folder.mkdir(parents=True)
    for name, data in payload.items():
        if name.startswith("theme/"):
            private_write(folder / name, data)
    config = restored_config(
        json.loads(payload["stack.json"]), manifest, target, folder
    )
    private_write(folder / "compose.json", json.dumps(config).encode())
    private_write(folder / "manifest.json", json.dumps(manifest).encode())
    print("创建独立数据卷并恢复三个数据库，原环境继续运行…", flush=True)
    try:
        run(
            "启动恢复数据库",
            target_compose(
                target, "up", "-d", "--wait", "--wait-timeout", "90", "postgres"
            ),
        )
        pg = containers(target)["postgres"]["Id"]
        actual = sql(
            pg, "postgres", "SELECT system_identifier FROM pg_control_system();"
        )
        if actual == manifest["source_system_identifier"]:
            raise trial.TrialError("目标不是新数据库集群，拒绝恢复。")
        # The vanilla target already has its bootstrap postgres role; all other
        # role ownership, passwords, memberships and grants come from the backup.
        roles = payload["roles.sql"].replace(
            b"CREATE ROLE postgres;", b"-- bootstrap role already exists"
        )
        run(
            "恢复数据库角色",
            [
                "docker",
                "exec",
                "-i",
                pg,
                "psql",
                "-X",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            roles,
        )
        for database in DATABASES:
            run(
                "恢复" + database,
                [
                    "docker",
                    "exec",
                    "-i",
                    pg,
                    "pg_restore",
                    "-U",
                    "postgres",
                    "--exit-on-error",
                    "--create",
                    "-d",
                    "postgres",
                ],
                payload[database + ".dump"],
            )
        set_limits(pg, manifest["limits"])
        counts = table_counts(pg)
        if counts != manifest["table_counts"]:
            raise trial.TrialError("恢复后表记录数与备份不一致；保留恢复现场。")
        business = sql(
            pg, "med_business", "SELECT version_num FROM med.alembic_version;"
        )
        control = sql(
            pg, "med_control", "SELECT version_num FROM control.alembic_version;"
        )
        rls = sql(
            pg,
            "med_business",
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON "
            "n.oid=c.relnamespace WHERE n.nspname='med' AND c.relrowsecurity AND c.relforcerowsecurity;",
        )
        expected = manifest["compatibility"]
        if (business, control, int(rls)) != (
            expected["business_revision"],
            expected["control_revision"],
            expected["business_rls_tables"],
        ):
            raise trial.TrialError("恢复后版本或租户隔离不符，不能启动应用。")
        private_write(
            folder / "ready.json",
            json.dumps(
                {
                    "target": target,
                    "backup_id": manifest["backup_id"],
                    "table_counts_match": True,
                    "tables": sum(map(len, counts.values())),
                    "source_system_identifier": manifest["source_system_identifier"],
                    "target_system_identifier": actual,
                    "rls_tables": int(rls),
                }
            ).encode(),
        )
    except BaseException:
        run("停止失败的恢复副本", target_compose(target, "stop", "--timeout", "30"))
        raise
    print(
        json.dumps(
            {"target": target, "table_counts_match": True, "ready_for_drill": True}
        )
    )


def assert_target(target):
    path = target_path(target)
    if not (path / "ready.json").exists() or not JOURNAL.exists():
        raise trial.TrialError("恢复副本未完成校验或原环境未进入维护。")
    state = containers(target)
    if any(
        not state.get(s, {}).get("State", {}).get("Running") for s in trial.SERVICES
    ):
        raise trial.TrialError("恢复副本六个服务未全部运行。")
    original = source()
    if any(original.get(s, {}).get("State", {}).get("Running") for s in WRITERS):
        raise trial.TrialError("原环境仍有写入服务，拒绝进行副本业务验收。")
    binding = state["web"]["NetworkSettings"]["Ports"].get("5173/tcp")
    if binding != [{"HostIp": "127.0.0.1", "HostPort": "5173"}]:
        raise trial.TrialError("入口未指向指定恢复副本。")


def start(target):
    if not (target_path(target) / "ready.json").exists():
        raise trial.TrialError("目标尚未完成恢复校验。")
    pause_source()
    try:
        free_port(5173)
        run(
            "启动恢复副本",
            target_compose(target, "up", "-d", "--wait", "--wait-timeout", "120"),
        )
        assert_target(target)
    except BaseException:
        run("停止恢复入口", target_compose(target, "stop", "--timeout", "30"))
        resume_source()
        raise
    print("入口已切至恢复副本；验收完成后执行 stop --target 恢复原环境。")


def stop(target):
    target_path(target)
    run("停止恢复副本", target_compose(target, "stop", "--timeout", "30"))
    resume_source()
    print("恢复副本已停止，保留演练数据卷；原环境已恢复。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "backup",
            "restore",
            "start",
            "stop",
            "assert-target",
            "resume-source",
        ],
    )
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.command == "backup":
        backup()
    elif args.command == "restore":
        if not args.backup or not args.key:
            raise trial.TrialError("恢复需指定 --backup 和 --key。")
        restore(args.backup, args.key)
    elif args.command == "resume-source":
        resume_source()
    else:
        if not args.target:
            raise trial.TrialError("需明确指定 --target 演练项目。")
        {"start": start, "stop": stop, "assert-target": assert_target}[args.command](
            args.target
        )


if __name__ == "__main__":
    try:
        main()
    except (trial.TrialError, OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        print(
            str(exc)
            if isinstance(exc, trial.TrialError)
            else "恢复配置或文件不可用；原始内容未展示。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
