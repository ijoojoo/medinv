"""Archive only recognized local acceptance fixtures; never delete ledger history.

Plan is read-only. Apply requires the exact saved plan and a verified encrypted
backup of this database cluster. Normal demo tenants and manual warehouses stay.
"""

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import recovery
import trial

ROOT = trial.ROOT
PROTECTED = {
    str(uuid5(NAMESPACE_URL, "medinv-dev/" + k)) for k in ("wholesale", "retail")
}
ACTOR = str(uuid5(NAMESPACE_URL, "medinv-dev/demo.owner"))
FIXTURES = {
    "API test": "backend/tests/integration/test_api.py",
    "Auth test": "backend/tests/integration/test_auth.py",
    "Expiry test": "backend/tests/integration/test_reservation_expiry.py",
    "空白企业": "backend/tests/integration/test_provisioning.py",
}
PREFIXES = {
    "试用开账企业": "enterprise-opening",
    "试用岗位企业": "job-handoff",
    "库存协作企业": "inventory-jobs",
    "药械包装营业企业": "mixed-packaging",
    "恢复验收企业": "recovery",
    "分批履约企业": "partial-fulfillment",
    "退货协作企业": "return-jobs",
    "连续营业企业": "continuous-operations",
}
WAREHOUSES = {
    "CQ": "盘点待办仓",
    "LOSS": "在途损失验收仓",
    "ML": "主线验收仓",
    "OP": "期初验收仓",
    "QA": "质量验收仓",
    "STOCK": "可售验收空仓",
    "TQ": "调拨待办仓",
    "WL": "多库位验收仓",
    "WT": "验收目的仓",
}


def database():
    return trial.run("Locate database", trial.compose("ps", "-q", "postgres")).strip()


def sql(pg, database_name, statement):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            pg,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database_name,
            "-At",
        ],
        input=statement.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise trial.TrialError("归档操作失败；当前事务已回滚，保留计划与备份供核对。")
    return result.stdout.decode("utf-8").strip()


def rows(pg, db, query):
    return json.loads(
        sql(pg, db, "SELECT coalesce(json_agg(x),'[]') FROM (" + query + ") x;")
    )


def literals(ids):
    return ",".join("'" + str(UUID(v)) + "'" for v in ids) or "NULL"


def inventory_proof(pg):
    tables = ("stock_balance", "cost_balance", "stock_ledger", "cost_ledger")
    result = rows(
        pg,
        "med_business",
        " UNION ALL ".join(
            "SELECT '" + table + "' AS name,count(*) AS rows,"
            "coalesce(md5(string_agg(to_jsonb(t)::text,'' ORDER BY id)),md5('')) AS digest "
            "FROM med." + table + " t WHERE tenant_id IN (" + literals(PROTECTED) + ")"
            for table in tables
        ),
    )
    return sorted(result, key=lambda r: r["name"])


def plan(pg):
    tenants = rows(
        pg, "med_control", "SELECT id,name,active FROM control.tenant ORDER BY id"
    )
    provisioning = {
        r["tenant_id"]: r["plan"]
        for r in rows(
            pg,
            "med_control",
            "SELECT tenant_id,plan FROM control.enterprise_provisioning",
        )
    }
    candidates = []
    for t in tenants:
        if t["id"] in PROTECTED:
            continue
        source = FIXTURES.get(t["name"])
        for prefix, test in PREFIXES.items():
            if re.fullmatch(re.escape(prefix) + r"-[0-9a-f]{8}", t["name"]):
                p = provisioning.get(t["id"], {})
                if (
                    p.get("tenant_name") == t["name"]
                    and str(uuid5(UUID(p["request_id"]), "tenant")) == t["id"]
                ):
                    source = "apps/admin-web/tests/" + test + ".spec.ts"
        if source and (ROOT / source).is_file():
            candidates.append({**t, "source": source})
    warehouses = rows(
        pg,
        "med_business",
        "SELECT tenant_id,id,entity_id,code,name,archived_at FROM med.warehouse WHERE tenant_id IN ("
        + literals(PROTECTED)
        + ") ORDER BY id",
    )
    chosen = []
    for w in warehouses:
        parts = w["code"].split("-", 1)
        if len(parts) != 2 or parts[0] not in WAREHOUSES:
            continue
        suffix = parts[1]
        pattern = r"[0-9a-f]{8}-[AB]" if parts[0] == "ML" else r"[a-z0-9]{8}"
        name = WAREHOUSES[parts[0]] + "-" + suffix
        if re.fullmatch(pattern, suffix) and w["name"] in {name, name + "-中心"}:
            chosen.append(w)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "cluster": sql(
            pg, "postgres", "SELECT system_identifier FROM pg_control_system();"
        ),
        "tenants": candidates,
        "warehouses": chosen,
        "preserved_tenants": [
            t for t in tenants if t["id"] not in {v["id"] for v in candidates}
        ],
        "preserved_warehouses": [
            w for w in warehouses if w["id"] not in {v["id"] for v in chosen}
        ],
        "policy": "停用已确认测试企业，归档已确认测试仓库；不删除库存、单据或审计历史。",
    }


def apply(pg, saved, backup):
    if not re.fullmatch(r"[0-9a-f]{32}", backup.stem):
        raise trial.TrialError("备份编号格式不正确。")
    key = ROOT / ".cache/recovery-keys" / (backup.stem + ".key")
    manifest, payload = recovery.unpack(backup, key)
    del payload
    if str(manifest["source_system_identifier"]) != saved["cluster"]:
        raise trial.TrialError("备份不属于计划中的数据库集群。")
    age = datetime.now(UTC) - datetime.fromisoformat(manifest["created_at"])
    if age.total_seconds() > 3600:
        raise trial.TrialError("请先创建一小时内的新备份再归档。")
    current = plan(pg)
    # Names/identities and source classification must still match the reviewed plan.
    for kind in ("tenants", "warehouses"):
        available = {v["id"]: v for v in current[kind]}
        for entry in saved[kind]:
            if (
                entry["id"] not in available
                or available[entry["id"]]["name"] != entry["name"]
            ):
                raise trial.TrialError("数据已变化，请重新生成清理计划。")
    if current["cluster"] != saved["cluster"]:
        raise trial.TrialError("计划不属于当前数据库。")
    tids, wids = (
        literals(t["id"] for t in saved["tenants"]),
        literals(w["id"] for w in saved["warehouses"]),
    )
    request = str(uuid4())
    recovery.pause_source()
    try:
        before_inventory = inventory_proof(pg)
        # Each database has a transaction. Re-applying the same plan is idempotent
        # if interruption occurs between them; completed changes are audited.
        sql(
            pg,
            "med_control",
            f"""BEGIN;
          WITH changed AS (UPDATE control.tenant SET active=false WHERE id IN ({tids}) AND active RETURNING id,name)
          INSERT INTO control.audit_event(id,tenant_id,actor_id,action,target_id,detail)
          SELECT gen_random_uuid(),id,'{ACTOR}','maintenance.test_data_archived',id,
            jsonb_build_object('name',name,'backup_id','{backup.stem}','request_id','{request}') FROM changed;
          UPDATE control.membership SET active=false,permission_version=permission_version+1 WHERE tenant_id IN ({tids}) AND active;
          UPDATE control.web_session SET revoked_at=now() WHERE tenant_id IN ({tids}) AND revoked_at IS NULL;
          COMMIT;""",
        )
        sql(
            pg,
            "med_business",
            f"""BEGIN;
          WITH changed AS (UPDATE med.legal_entity SET status='SUSPENDED',version=version+1,updated_at=now(),updated_by='{ACTOR}' WHERE tenant_id IN ({tids}) AND status<>'SUSPENDED' RETURNING tenant_id,id,name)
          INSERT INTO med.audit_event(tenant_id,id,created_by,actor_subject,action,resource_type,resource_id,request_id,reason)
          SELECT tenant_id,gen_random_uuid(),'{ACTOR}','{ACTOR}','maintenance.test_data_archived','legal_entity',id,'{request}',jsonb_build_object('name',name,'backup_id','{backup.stem}')::text FROM changed;
          WITH changed AS (UPDATE med.warehouse SET archived_at=now(),version=version+1,updated_at=now(),updated_by='{ACTOR}' WHERE id IN ({wids}) AND tenant_id IN ({literals(PROTECTED)}) AND archived_at IS NULL RETURNING tenant_id,id,name)
          INSERT INTO med.audit_event(tenant_id,id,created_by,actor_subject,action,resource_type,resource_id,request_id,reason)
          SELECT tenant_id,gen_random_uuid(),'{ACTOR}','{ACTOR}','maintenance.test_data_archived','warehouse',id,'{request}',jsonb_build_object('name',name,'backup_id','{backup.stem}')::text FROM changed;
          COMMIT;""",
        )
        after_inventory = inventory_proof(pg)
        if before_inventory != after_inventory:
            raise trial.TrialError("库存与成本校验不一致，保留备份及审计记录以供核对。")
    finally:
        recovery.resume_source()
    disabled = rows(
        pg,
        "med_control",
        f"SELECT count(*) AS n FROM control.audit_event WHERE action='maintenance.test_data_archived' AND detail->>'request_id'='{request}'",
    )[0]["n"]
    archived = rows(
        pg,
        "med_business",
        f"SELECT count(*) AS n FROM med.audit_event WHERE action='maintenance.test_data_archived' AND request_id='{request}' AND resource_type='warehouse'",
    )[0]["n"]
    suspended = rows(
        pg,
        "med_business",
        f"SELECT count(*) AS n FROM med.audit_event WHERE action='maintenance.test_data_archived' AND request_id='{request}' AND resource_type='legal_entity'",
    )[0]["n"]
    return {
        "backup_id": backup.stem,
        "request_id": request,
        "newly_disabled_tenants": disabled,
        "suspended_entities": suspended,
        "archived_warehouses": archived,
        "remaining_demo_warehouses": current["preserved_warehouses"],
        "inventory_history_unchanged": before_inventory == after_inventory,
        "inventory_proof": after_inventory,
        "completed_at": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        pg = database()
        if args.command == "plan":
            result = plan(pg)
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "matched_tenants": len(result["tenants"]),
                        "warehouses": len(result["warehouses"]),
                        "preserved_tenants": len(result["preserved_tenants"]),
                    }
                )
            )
        else:
            if not args.backup:
                raise trial.TrialError("归档前必须指定已验证的新备份。")
            saved = json.loads(args.plan.read_text(encoding="utf-8"))
            result = apply(pg, saved, args.backup)
            args.plan.with_suffix(".result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k != "remaining_demo_warehouses"
                    }
                )
            )
    except trial.TrialError as exc:
        raise SystemExit(str(exc)) from None
