"""Build and operate the local trial stack; never migrates, seeds or deletes data.

Uses only Python's standard library. Existing databases and Keycloak must already
be initialized according to README. Runtime needs Python 3.11+ and Docker Compose;
building additionally needs the project's Node/pnpm dependencies.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache/trial"
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "MED_RUNTIME_PASSWORD",
    "MED_MIGRATOR_PASSWORD",
    "CONTROL_RUNTIME_PASSWORD",
    "CONTROL_MIGRATOR_PASSWORD",
    "CONTROL_AUTH_PASSWORD",
    "KEYCLOAK_DB_PASSWORD",
    "KEYCLOAK_ADMIN_PASSWORD",
    "OIDC_CLIENT_SECRET",
    "DEMO_USER_PASSWORD",
)
SERVICES = ("postgres", "keycloak", "web", "core", "auth", "expiry")


class TrialError(Exception):
    pass


def read_secrets(path=None):
    path = path or ROOT / "deploy/.env"
    if not path.is_file():
        raise TrialError(
            "缺少 deploy/.env；请按安装文档初始化，已有数据库须保留原密钥。"
        )
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise TrialError("配置存在重复项或格式错误；应为单行 KEY=value。")
        values[key] = value
    for key in SECRET_KEYS:
        value = values.get(key, "")
        # The local initializer generates URL-safe random secrets. Avoid dotenv
        # interpolation/quoting ambiguity when the same file is read by Compose.
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,}", value) or any(
            word in value.lower() for word in ("replace-with", "generate-with")
        ):
            raise TrialError(
                f"{key} 缺失、仍为模板或格式不正确；请使用本机初始化工具生成。"
            )
    if len({values[key] for key in SECRET_KEYS}) != len(SECRET_KEYS):
        raise TrialError("不同用途的密钥不得共用；已有数据库请按凭据维护流程处理。")
    try:
        encoded = values["SESSION_ENCRYPTION_KEY"]
        if len(base64.b64decode(encoded, altchars=b"-_", validate=True)) != 32:
            raise ValueError()
    except (KeyError, ValueError) as exc:
        raise TrialError("SESSION_ENCRYPTION_KEY 缺失或不是有效的32字节密钥。") from exc
    return values


def compatibility():
    return json.loads(
        (ROOT / "deploy/trial/compatibility.json").read_text(encoding="utf-8")
    )


def run(label, args, *, timeout=600):
    try:
        result = subprocess.run(
            [str(a) for a in args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrialError(
            f"{label}未完成；请检查命令是否安装、Docker是否启动。"
        ) from exc
    if result.returncode:
        # Do not surface raw environment/connection strings from failed commands.
        output = result.stdout + result.stderr
        try:
            values = read_secrets()
            for key in (*SECRET_KEYS, "SESSION_ENCRYPTION_KEY"):
                output = output.replace(values[key], "[已隐藏]")
        except TrialError:
            output = "配置不可用，原始输出未保存。"
        output = re.sub(r"postgresql[^\s\"']+", "[连接地址已隐藏]", output)
        CACHE.mkdir(parents=True, exist_ok=True)
        log = CACHE / "last-operation.log"
        log.write_text(output[-12000:], encoding="utf-8")
        log.chmod(0o600)
        raise TrialError(
            f"{label}失败；检查 .cache/trial/last-operation.log，数据未被清理。"
        )
    return result.stdout


def compose(*args, application=True):
    command = [
        "docker",
        "compose",
        "--project-name",
        compatibility()["project"],
        "--env-file",
        ROOT / "deploy/.env",
        "-f",
        ROOT / "deploy/compose.yaml",
    ]
    if application:
        command.extend(["-f", ROOT / "deploy/compose.trial.yaml"])
    return [*command, *args]


def runtime_values(values):
    def url(role, key, database):
        return f"postgresql+psycopg://{role}:{quote(values[key], safe='')}@postgres:5432/{database}"

    origin = compatibility()["public_origin"]
    core = {
        "MED_DATABASE_URL": url("med_runtime", "MED_RUNTIME_PASSWORD", "med_business"),
        "MED_CONTROL_DATABASE_URL": url(
            "control_runtime", "CONTROL_RUNTIME_PASSWORD", "med_control"
        ),
        "MED_PUBLIC_ORIGIN": origin,
    }
    for area in (
        "PROCUREMENT",
        "SALES",
        "SETTLEMENT",
        "RETURNS",
        "RETAIL",
        "COUNT",
        "QUALITY",
        "TRANSFER",
        "WAREHOUSE",
        "OPENING",
    ):
        core[f"MED_{area}_COMMANDS_ENABLED"] = "true"
    core["MED_RESERVATION_EXPIRY_ENABLED"] = "true"
    auth = {
        "MED_ENVIRONMENT": "development",
        "MED_CONTROL_AUTH_DATABASE_URL": url(
            "control_auth", "CONTROL_AUTH_PASSWORD", "med_control"
        ),
        "MED_PUBLIC_ORIGIN": origin,
        "MED_OIDC_ISSUER_URL": origin + "/identity/realms/medinv",
        "MED_OIDC_CLIENT_SECRET": values["OIDC_CLIENT_SECRET"],
        "MED_SESSION_ENCRYPTION_KEY": values["SESSION_ENCRYPTION_KEY"],
    }
    return {"core": core, "auth": auth}


def write_runtime():
    values = runtime_values(read_secrets())
    CACHE.mkdir(parents=True, exist_ok=True)
    for service, settings in values.items():
        path = CACHE / (service + ".env")
        content = "".join(f"{k}={v}\n" for k, v in settings.items())
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)
    # These files contain only runtime credentials, never migration/owner/admin keys.


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_files():
    directories = (
        "backend/src",
        "apps/admin-web/src",
        "apps/admin-web/dist",
        "deploy/trial",
    )
    files = [
        ROOT / p
        for p in (
            ".dockerignore",
            "deploy/Dockerfile",
            "deploy/Dockerfile.web",
            "deploy/Dockerfile.web.dockerignore",
            "deploy/requirements.lock.txt",
            "deploy/compose.yaml",
            "deploy/compose.trial.yaml",
            "apps/admin-web/package.json",
            "apps/admin-web/pnpm-lock.yaml",
            "apps/admin-web/index.html",
            "apps/admin-web/vite.config.ts",
            "scripts/trial.py",
        )
    ]
    for folder in directories:
        files.extend(
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    if not (ROOT / "apps/admin-web/dist/index.html").is_file():
        raise TrialError("缺少管理端构建文件，请先执行 build。")
    if any(p.is_symlink() for p in files):
        raise TrialError("交付源码不可使用符号链接，请核对构建目录。")
    return sorted(set(files))


def release_path():
    path = ROOT / "release.json"
    return path if path.exists() else CACHE / "release.json"


def load_release():
    path = release_path()
    if not path.is_file():
        raise TrialError("缺少构建清单，请先执行 build；交付包请先执行 load。")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_release(*, images=True):
    release = load_release()
    if release["compatibility"] != compatibility():
        raise TrialError("交付配置与构建清单不一致，请重新构建或重新解压完整交付包。")
    for relative, expected in release["files"].items():
        path = (ROOT / relative).resolve()
        if (
            not path.is_relative_to(ROOT)
            or not path.is_file()
            or digest(path) != expected
        ):
            raise TrialError("源码或构建产物已变化，请重新执行 build。")
    if images:
        for name, image in release["images"].items():
            actual = run(
                "核对" + name + "镜像",
                ["docker", "image", "inspect", image["tag"], "--format", "{{.Id}}"],
            )
            if actual.strip() != image["id"]:
                raise TrialError(f"{name}镜像与构建清单不符，请重新构建或加载交付包。")
    return release


def rows():
    raw = run(
        "读取服务状态", compose("ps", "--all", "--format", "json", application=False)
    )
    if not raw.strip():
        return {}
    data = (
        json.loads(raw)
        if raw.lstrip().startswith("[")
        else [json.loads(line) for line in raw.splitlines()]
    )
    return {
        r["Service"]: {"state": r["State"], "health": r.get("Health", "")}
        for r in data
        if r["Service"] in SERVICES
    }


def check_ports(state):
    for port in (5173, 8000, 8001):
        if port == 5173 and state.get("web", {}).get("state") == "running":
            continue
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise TrialError(
                    f"本机{port}端口已被其他进程占用，请先停止原开发服务；工具不会终止外部进程。"
                )


def check_schema():
    expected = compatibility()
    found = {}
    for database, schema in (("med_business", "med"), ("med_control", "control")):
        query = f"BEGIN READ ONLY; SELECT version_num FROM {schema}.alembic_version; COMMIT;"
        found[schema] = run(
            "读取数据库修订",
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                database,
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
                application=False,
            ),
        ).strip()
    if found != {
        "med": expected["business_revision"],
        "control": expected["control_revision"],
    }:
        raise TrialError(
            "数据库修订与交付版本不一致；请先备份并按工程安装/升级步骤迁移。启动不会自动迁移。"
        )
    return found


def readiness():
    result = {}
    origin = compatibility()["public_origin"]
    for name, path in (
        ("web", "/health/web"),
        ("core", "/health/core"),
        ("auth", "/health/auth"),
        ("identity", "/identity/realms/medinv/.well-known/openid-configuration"),
    ):
        try:
            with urllib.request.urlopen(origin + path, timeout=6) as response:
                data = json.load(response)
                result[name] = (
                    data.get("issuer") == origin + "/identity/realms/medinv"
                    if name == "identity"
                    else data.get("status") == "ok"
                )
        except (OSError, ValueError):
            result[name] = False
    state = rows()
    result["expiry_running"] = state.get("expiry", {}).get("state") == "running"
    result["containers_running"] = all(
        state.get(s, {}).get("state") == "running" for s in SERVICES
    )
    print(
        json.dumps({"services": state, "ready": result}, ensure_ascii=False, indent=2)
    )
    if not all(result.values()):
        raise TrialError("部分服务未就绪；先核对服务状态，修复后再次执行 start。")


def build():
    read_secrets()
    print("构建管理页面及两个运行镜像…", flush=True)
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if not pnpm:
        raise TrialError("未找到pnpm；构建需要先安装工程锁定的前端依赖。")
    run("构建管理页面", [pnpm, "--dir", "apps/admin-web", "build"])
    config = compatibility()
    for name, dockerfile in (
        ("core", "deploy/Dockerfile"),
        ("web", "deploy/Dockerfile.web"),
    ):
        run(
            "构建" + name + "镜像",
            ["docker", "build", "-f", dockerfile, "-t", config["images"][name], "."],
        )
    release = {
        "created_at": datetime.now(UTC).isoformat(),
        "compatibility": config,
        "files": {p.relative_to(ROOT).as_posix(): digest(p) for p in source_files()},
        "images": {
            name: {
                "tag": tag,
                "id": run(
                    "读取镜像标识",
                    ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
                ).strip(),
            }
            for name, tag in config["images"].items()
        },
    }
    write_runtime()
    release_path().write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("构建完成。镜像与源文件指纹已保存；运行配置保存在私有缓存目录。")


def package(output):
    release = verify_release()
    output = output.resolve()
    if output.exists():
        raise TrialError("目标交付包已存在，请使用新文件名以保留原交付。")
    output.parent.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    # Temporary files stay in the explicitly resolved workspace cache directory.
    with tempfile.TemporaryDirectory(
        prefix="bundle-", dir=CACHE.resolve()
    ) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(CACHE.resolve()):
            raise TrialError("交付暂存路径不正确。")
        print("导出业务与管理端镜像，整理不含密钥的交付包…", flush=True)
        archive = stage / "images.tar"
        run(
            "导出镜像",
            [
                "docker",
                "image",
                "save",
                "-o",
                archive,
                *compatibility()["images"].values(),
            ],
        )
        release["images_archive_sha256"] = digest(archive)
        selected = set(source_files())
        for folder in ("scripts", "deploy", "backend/migrations", "contracts"):
            selected.update(
                p
                for p in (ROOT / folder).rglob("*")
                if p.is_file()
                and "__pycache__" not in p.parts
                and p.name != ".env"
                and not p.name.startswith(".env.")
                and p.suffix != ".pyc"
            )
        selected.add(ROOT / "deploy/.env.example")
        selected.update(
            ROOT / "backend" / filename
            for filename in (
                "pyproject.toml",
                "uv.lock",
                "alembic-business.ini",
                "alembic-control.ini",
            )
        )
        selected.update((ROOT / "apps/admin-web").glob("tsconfig*.json"))
        # Include the existing initialization documentation, not any local artifacts.
        selected.add(ROOT / "README.md")
        selected.update((ROOT / "docs").rglob("*.md"))
        if any(
            p.is_symlink() or not p.resolve().is_relative_to(ROOT) for p in selected
        ):
            raise TrialError("交付清单含外部路径或符号链接。")
        with zipfile.ZipFile(
            output, "x", zipfile.ZIP_DEFLATED, compresslevel=3
        ) as bundle:
            for path in sorted(selected):
                bundle.write(path, path.relative_to(ROOT).as_posix())
            bundle.write(archive, "images.tar")
            bundle.writestr(
                "release.json", json.dumps(release, ensure_ascii=False, indent=2) + "\n"
            )
        checksum = digest(output)
        output.with_suffix(output.suffix + ".sha256").write_text(
            checksum + "  " + output.name + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "package": str(output),
                "sha256": checksum,
                "bytes": output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["build", "check", "start", "stop", "status", "package", "load"],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build()
    elif args.command == "load":
        release = verify_release(images=False)
        archive = ROOT / "images.tar"
        if not archive.is_file() or digest(archive) != release.get(
            "images_archive_sha256"
        ):
            raise TrialError("镜像归档缺失或校验失败。")
        run("加载交付镜像", ["docker", "load", "-i", archive])
        verify_release()
        print("交付镜像加载并核对完成；数据库与身份资料未修改。")
    elif args.command == "package":
        if not args.output:
            raise TrialError("请通过 --output 指定新的交付包路径。")
        package(args.output)
    else:
        read_secrets()
        run(
            "检查Docker",
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            timeout=20,
        )
        if args.command == "status":
            readiness()
        elif args.command == "stop":
            # Base compose can stop the named application containers only if its
            # service declarations exist; use the generated overlay after a start.
            if not (CACHE / "core.env").exists() or not (CACHE / "auth.env").exists():
                raise TrialError(
                    "缺少本机运行配置；请核对部署目录，不能停止不属于此部署的进程。"
                )
            print("停止应用与基础服务，保留数据库卷和单据…", flush=True)
            run("停止试用环境", compose("stop", "--timeout", "30", *reversed(SERVICES)))
            print("已停止；数据卷、密钥与构建记录均保留。")
        else:
            verify_release()
            state = rows()
            check_ports(state)
            if args.command == "check":
                schema = check_schema()
                print(
                    json.dumps(
                        {
                            "configuration": "valid",
                            "images": "match",
                            "database_revisions": schema,
                            "services": state,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                write_runtime()
                run("验证容器配置", compose("config", "--quiet"))
                print("启动基础服务并核对数据库版本…", flush=True)
                run(
                    "启动数据库",
                    compose(
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "90",
                        "postgres",
                        application=False,
                    ),
                )
                check_schema()
                if not (ROOT / ".cache/keycloak/medinv-realm.json").is_file():
                    raise TrialError(
                        "缺少本机身份导入配置，请先按README完成身份初始化。"
                    )
                print("启动页面、业务、认证与预占回收…", flush=True)
                run(
                    "启动试用环境",
                    compose("up", "-d", "--wait", "--wait-timeout", "120", *SERVICES),
                )
                readiness()
                print("试用环境已就绪：http://127.0.0.1:5173")


if __name__ == "__main__":
    try:
        main()
    except (TrialError, ValueError, KeyError) as exc:
        # JSON/config parsing errors must not print secret-bearing raw lines.
        print(
            str(exc) if isinstance(exc, TrialError) else "配置或构建清单格式不正确。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
