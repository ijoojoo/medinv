"""Prepare loopback-only Keycloak development resources; no production credentials."""

import json
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dev_accounts import ACCOUNTS

ROOT = Path(__file__).resolve().parents[1]
env = dict(
    line.split("=", 1)
    for line in (ROOT / "deploy/.env").read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
)
folder = ROOT / ".cache/keycloak"
folder.mkdir(parents=True, exist_ok=True)


def identity(name):
    return str(uuid5(NAMESPACE_URL, "medinv-dev/" + name))


realm = {
    "realm": "medinv",
    "enabled": True,
    "displayName": "药械云",
    "loginTheme": "medinv",
    "internationalizationEnabled": True,
    "supportedLocales": ["zh-CN"],
    "defaultLocale": "zh-CN",
    "registrationAllowed": False,
    "resetPasswordAllowed": False,
    "rememberMe": False,
    "revokeRefreshToken": True,
    "refreshTokenMaxReuse": 0,
    "accessTokenLifespan": 300,
    "ssoSessionIdleTimeout": 1800,
    "ssoSessionMaxLifespan": 28800,
    "clients": [
        {
            "clientId": "medinv-web",
            "name": "药械进销存管理端",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "secret": env["OIDC_CLIENT_SECRET"],
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "redirectUris": ["http://127.0.0.1:5173/auth/callback"],
            "webOrigins": ["http://127.0.0.1:5173"],
            "attributes": {"pkce.code.challenge.method": "S256"},
        }
    ],
    "users": [
        {
            "id": identity(name),
            "username": name,
            "enabled": True,
            "emailVerified": True,
            "firstName": label,
            "lastName": "本机演示",
            "email": name + "@example.invalid",
            "credentials": [
                {
                    "type": "password",
                    "value": env["DEMO_USER_PASSWORD"],
                    "temporary": False,
                }
            ],
        }
        for name, label in ACCOUNTS
    ],
}
(folder / "medinv-realm.json").write_text(
    json.dumps(realm, ensure_ascii=False, indent=2), encoding="utf-8"
)
(ROOT / ".cache/演示账号.md").write_text(
    "# 仅限本机开发的演示账号\n\n管理端：http://127.0.0.1:5173\n\n"
    + "管理员：`demo.owner`\n\n只读成员：`demo.viewer`\n\n"
    + "质量审核员：`demo.quality`（审核资料、采购单，验收并过账）\n\n"
    + "\n\n".join(label + "：`" + name + "`" for name, label in ACCOUNTS[3:])
    + "\n\n"
    + "开发账号的初始密码：`"
    + env["DEMO_USER_PASSWORD"]
    + "`\n\n"
    + "新增岗位账号需要由平台加入试用企业后才能登录；已有密码不重置。\n\n"
    + "管理员负责建档、制单和收货；质量审核员负责独立审核及验收入库。\n\n"
    + "账号仅创建在本机 Keycloak。不要用于生产或提交到版本库。\n",
    encoding="utf-8",
)
sql = (
    "\\set auth '"
    + env["CONTROL_AUTH_PASSWORD"]
    + "'\n\\set kc '"
    + env["KEYCLOAK_DB_PASSWORD"]
    + "'\n"
)
sql += (ROOT / "deploy/postgres/identity-roles.sql").read_text(encoding="utf-8")
subprocess.run(
    [
        "docker",
        "compose",
        "--env-file",
        "deploy/.env",
        "-f",
        "deploy/compose.yaml",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "postgres",
    ],
    input=sql,
    text=True,
    cwd=ROOT,
    check=True,
    capture_output=True,
)
print("Keycloak import prepared. Demo credentials saved privately under .cache/.")
