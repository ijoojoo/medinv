import json
import re
import unicodedata
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.master import SchemaPublish, SchemaSave
from medinv.modules.catalog.application.dictionaries import DEFINITIONS as DICTIONARIES
from medinv.modules.catalog.application.dictionaries import current_dictionary, dictionary_lock
from medinv.modules.catalog.application.dictionary_rules import (
    MASTER_DICTIONARY_KEYS,
    master_dictionary_bindings,
)
from medinv.modules.catalog.domain.master_schema import (
    FIELD_MAP,
    FIELDS,
    PROFILES,
    ROLE_LABELS,
    SUBTYPES,
    TABS,
    default_schema,
    descendants,
    node_path,
    stable_id,
)
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def schema_lock(repo: SqlSession, ctx: TenantContext) -> None:
    repo.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
        {"key": f"master-schema:{ctx.tenant_id}"},
    )


def business_rules_changed(
    repo: SqlSession, previous_revision: int | None, profile: str, current: dict[str, Any]
) -> bool:
    if previous_revision is None or previous_revision == current["revision"]:
        return False
    rows = repo.rows(
        "SELECT payload FROM med.catalog_schema WHERE state='PUBLISHED' AND revision=:v",
        {"v": previous_revision},
    )
    if not rows:
        return True

    def constraints(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: {k: v for k, v in rule.items() if k not in {"label", "read_only"}}
            for key, rule in payload["rules"][profile].items()
        }

    before, after = constraints(rows[0]["payload"]), constraints(current["payload"])
    if any(rule != after.get(key) for key, rule in before.items()):
        return True
    # Optional appended fields have no old values to invalidate; they must not turn
    # an ordinary operating/price edit into a quality resubmission by themselves.
    return any(
        rule.get("applicable") and (rule.get("required") or rule.get("required_when"))
        for key, rule in after.items()
        if key not in before
    )


def current_schema(repo: SqlSession) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.catalog_schema WHERE state='PUBLISHED' ORDER BY revision DESC LIMIT 1",
        {},
    )
    if rows:
        return rows[0]
    payload = default_schema()
    # Preserve applicable legacy policy aliases/requirements when first adopting MDM.
    policies = repo.rows("SELECT category,fields AS rules FROM med.product_field_rules", {})
    for policy in policies:
        profile = {"DRUG": "drug", "DEVICE": "device"}.get(policy["category"])
        if profile:
            for rule in policy["rules"]:
                target = payload["rules"][profile].get(rule.get("key"))
                if target:
                    target["required"] = target["required"] or bool(rule.get("required"))
                    if FIELD_MAP[rule["key"]]["alias_allowed"]:
                        target["label"] = rule.get("display_name", "")
    return {"id": None, "revision": 0, "version": 0, "state": "PUBLISHED", "payload": payload}


def persist_nodes(repo: SqlSession, ctx: TenantContext, payload: dict[str, Any]) -> None:
    for node in payload["nodes"]:
        repo.execute(
            (
                "INSERT INTO med.catalog_category\n"
                "          (tenant_id,id,node_code,name,parent_id,sort_order,enabl"
                "ed,is_assignable,field_profile_id)\n"
                "          VALUES(:t,:node_id,:node_code,:name,:parent_id,:sort_or"
                "der,:enabled,:is_assignable,:field_profile_id)\n"
                "          ON CONFLICT(tenant_id,id) DO UPDATE SET "
                "name=:name,parent_id=:parent_id,sort_order=:sort_order,\n"
                "          enabled=:enabled,is_assignable=:is_assignable,field_pro"
                "file_id=:field_profile_id"
            ),
            {"t": ctx.tenant_id, **node},
        )


def ensure_schema(repo: SqlSession, ctx: TenantContext) -> dict[str, Any]:
    current = current_schema(repo)
    if current["revision"] == 0:
        persist_nodes(repo, ctx, current["payload"])
        # Revision zero is a real immutable initial snapshot once the first product is written.
        repo.execute(
            (
                "INSERT INTO med.catalog_schema(tenant_id,id,revision,state,base_r"
                "evision,payload,created_by,updated_by,published_at)\n"
                "          VALUES(:t,:id,0,'PUBLISHED',0,CAST(:payload AS "
                "jsonb),:actor,:actor,now())"
            ),
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "payload": json.dumps(current["payload"]),
                "actor": ctx.principal_id,
            },
        ) if current["id"] is None else None
    return current


def configuration_errors(payload: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    try:
        return _configuration_errors(payload, previous)
    except TypeError, ValueError, KeyError, AttributeError:
        return ["配置结构或字段类型不合法，请使用系统配置编辑器"]


def _configuration_errors(payload: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(payload) != set(default_schema()) or payload.get("format") != 1:
        return ["配置结构或格式版本无效"]
    if payload["tabs"] != [{"id": k, "label": v} for k, v in TABS]:
        errors.append("七个公共页签及顺序必须保留")
    fields = payload.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 150:
        return ["字段目录无效或超过 150 项"]
    by_key = {f.get("key"): f for f in fields if isinstance(f, dict)}
    if len(by_key) != len(fields):
        errors.append("字段键不得重复")
    for original in FIELDS:
        if by_key.get(original["key"]) != original:
            errors.append(f"内置字段语义不可修改：{original['key']}")
    old_fields = {f["key"]: f for f in previous["fields"]}
    for k, f in by_key.items():
        if k in FIELD_MAP:
            continue
        if not isinstance(k, str) or not re.fullmatch(r"custom_[a-z][a-z0-9_]{0,40}", k):
            errors.append("自定义字段键须为 custom_ 开头的稳定英文标识")
            continue
        if (
            f.get("binding_key") != "custom." + k
            or f.get("tab") != "custom"
            or f.get("value_type") not in {"text", "integer", "decimal", "boolean", "date"}
            or f.get("data_scope") != "PRODUCT"
            or f.get("field_id") != stable_id("field:" + k)
            or f.get("read_permission") not in {"catalog.read", "catalog.review"}
            or f.get("write_permission") != "catalog.write"
            or f.get("lifecycle") not in {"CUSTOM", "INACTIVE"}
        ):
            errors.append(f"自定义字段类型、权限或绑定无效：{k}")
        if k in old_fields and any(
            f.get(p) != old_fields[k].get(p)
            for p in ("field_id", "value_type", "binding_key", "semantic_definition", "data_scope")
        ):
            errors.append(f"已有字段身份、类型和语义不可更改：{k}")
    if set(old_fields) - set(by_key):
        errors.append("字段只能停用，不能删除或回收身份")
    slots = payload.get("slots", [])
    if not isinstance(slots, list) or len(slots) != len(fields):
        errors.append("每个字段必须且只能有一个公共槽位")
        slots = []
    ids, locations, bindings = set(), set(), set()
    for slot in slots:
        if set(slot) != {"slot_id", "field_key", "tab", "position", "height", "span"}:
            errors.append("槽位属性不合法")
            continue
        location = (slot["tab"], slot["position"])
        if slot["slot_id"] in ids or location in locations or slot["field_key"] in bindings:
            errors.append("槽位重叠、重复身份或重复写入绑定")
        ids.add(slot["slot_id"])
        locations.add(location)
        bindings.add(slot["field_key"])
        if (
            slot["field_key"] not in by_key
            or slot["tab"] != by_key[slot["field_key"]].get("tab")
            or type(slot["position"]) is not int
            or not 0 <= slot["position"] < 150
            or slot["height"] != 94
            or slot["span"] != 1
        ):
            errors.append("槽位越界或尺寸不符合公共布局")
    for original in default_schema()["slots"][:15]:
        actual = next((s for s in slots if s.get("slot_id") == original["slot_id"]), None)
        if actual != original:
            errors.append(f"首版基本信息固定槽位须保留：{original['slot_id']}")
    nodes = payload.get("nodes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200:
        return errors + ["品类节点数量无效"]
    indexed = {n.get("node_id"): n for n in nodes if isinstance(n, dict)}
    old_nodes = {n["node_id"]: n for n in previous["nodes"]}
    if len(indexed) != len(nodes) or len({n.get("node_code") for n in nodes}) != len(nodes):
        errors.append("品类 ID 或代码重复")
    if set(old_nodes) - set(indexed):
        errors.append("已有节点不能删除，请停用以保留历史")
    for node in nodes:
        try:
            UUID(node["node_id"])
            assert set(node) == set(default_schema()["nodes"][0])
            assert isinstance(node["name"], str) and 0 < len(node["name"].strip()) <= 120
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,59}", node["node_code"])
            assert type(node["sort_order"]) is int
            assert type(node["enabled"]) is bool and type(node["is_assignable"]) is bool
            assert node["field_profile_id"] is None or node["field_profile_id"] in PROFILES
        except KeyError, ValueError, TypeError, AssertionError:
            errors.append("品类节点结构或数据无效")
            continue
        prior = old_nodes.get(str(node["node_id"]))
        if prior and any(node[k] != prior[k] for k in ("node_code", "field_profile_id")):
            errors.append(f"节点代码和监管品类不可静默更换：{node['name']}")
        if node["is_assignable"] and node["field_profile_id"] not in PROFILES:
            errors.append(f"允许建档节点必须绑定支持的完整品类：{node['name']}")
        parent, seen = node["parent_id"], {node["node_id"]}
        while parent:
            if parent not in indexed or parent in seen:
                errors.append(f"父节点不存在或存在循环：{node['name']}")
                break
            seen.add(parent)
            parent = indexed[parent]["parent_id"]
        if node["parent_id"] is None and node["node_code"] != "catalog_root":
            errors.append("所有节点须归属于唯一的全部商品与项目根节点")
    rules = payload.get("rules", {})
    if set(rules) != set(PROFILES):
        return errors + ["必须提供全部品类的规则快照"]
    for profile, category_rules in rules.items():
        if set(category_rules) != set(by_key):
            errors.append(f"品类字段配置不完整：{profile}")
            continue
        names: dict[str, str] = {}
        reserved = {
            "".join(unicodedata.normalize("NFKC", f["default_label"]).casefold().split()): k
            for k, f in by_key.items()
        }
        for key, rule in category_rules.items():
            required_keys = {"applicable", "label", "required", "read_only", "required_when"}
            optional_keys = {"min_length", "max_length", "validator"}
            if not required_keys <= set(rule) or set(rule) - required_keys - optional_keys:
                errors.append("品类规则不能包含布局或存储绑定属性")
                continue
            if (
                type(rule.get("min_length", 0)) is not int
                or type(rule.get("max_length", 500)) is not int
                or not 0 <= rule.get("min_length", 0) <= rule.get("max_length", 500) <= 500
                or rule.get("validator", "NONE")
                not in {"NONE", "NO_WHITESPACE", "ASCII_CODE", "DIGITS"}
            ):
                errors.append(f"字段长度或格式规则无效：{profile}/{key}")
            f = by_key[key]
            if (
                key in DICTIONARIES
                and type(rule.get("min_length", 0)) is int
                and rule.get("min_length", 0) > DICTIONARIES[key][1]
            ):
                errors.append(f"最小长度超出字段容量：{profile}/{key}")
            if any(type(rule[x]) is not bool for x in ("applicable", "required", "read_only")):
                errors.append("适用、必填及只读必须为布尔值")
            if not isinstance(rule["label"], str) or len(rule["label"]) > 40:
                errors.append("别名须为 40 字以内文本")
                continue
            if rule["label"] and not f.get("alias_allowed"):
                errors.append(f"专业角色不能任意改名：{key}")
            alias = "".join(unicodedata.normalize("NFKC", rule["label"]).casefold().split())
            if alias in reserved and reserved[alias] != key:
                errors.append(f"别名不能占用其他业务字段的名称：{key}")
            if rule["applicable"] and profile not in f["profiles"]:
                errors.append(f"字段不支持该监管品类：{profile}/{key}")
            if (rule["required"] or rule["required_when"]) and not rule["applicable"]:
                errors.append(f"不适用字段不能必填：{profile}/{key}")
            if (
                key in {"name", "base_unit"}
                and profile in f["profiles"]
                and (not rule["required"] or not rule["applicable"])
            ):
                errors.append(f"系统基础身份规则不得取消：{profile}/{key}")
            condition = rule["required_when"]
            if condition:
                allowed = {
                    "regulatory_subtype": {v for v, _ in SUBTYPES[profile]},
                    "device_class": {"I", "II", "III"},
                }
                if (
                    not isinstance(condition, dict)
                    or set(condition) != {"field", "equals"}
                    or condition.get("equals")
                    not in allowed.get(str(condition.get("field")), set())
                ):
                    errors.append("条件必填仅接受监管子类型或器械类别的受控比较")
            name = "".join(
                unicodedata.normalize("NFKC", rule["label"] or f["default_label"])
                .casefold()
                .split()
            )
            if name in names and names[name] != key:
                errors.append(f"同一品类的字段标签重复：{profile}/{name}")
            names[name] = key
    if payload["columns"] != [
        {
            "key": f["key"],
            "label": f["default_label"],
            "value_type": f["value_type"],
            "projection": f["key"],
        }
        for f in fields
    ]:
        errors.append("查询列必须使用固定字段键和统一投影")
    return sorted(set(errors))


def preview(repo: SqlSession, payload: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    errors = configuration_errors(payload, previous)
    if errors:
        return {"errors": errors, "impacts": [], "preview_token": ""}
    dictionaries = {key: current_dictionary(repo, key) for key in MASTER_DICTIONARY_KEYS}
    checks = [
        binding
        for key, dictionary in dictionaries.items()
        for binding in master_dictionary_bindings(payload, key, dictionary)
    ]
    counts = repo.rows(
        (
            "SELECT category_node_id,category,count(*) AS count FROM "
            "med.product GROUP BY category_node_id,category"
        ),
        {},
    )
    impacts = []
    old = {n["node_id"]: n for n in previous["nodes"]}
    for n in payload["nodes"]:
        changed = n["node_id"] not in old or old[n["node_id"]] != n
        if changed:
            branch = descendants(payload["nodes"], n["node_id"])
            impacts.append(
                {
                    "node_id": n["node_id"],
                    "path": node_path(payload["nodes"], n["node_id"]),
                    "before": old.get(n["node_id"]),
                    "after": n,
                    "products": sum(
                        r["count"]
                        for r in counts
                        if (
                            str(r["category_node_id"])
                            if r["category_node_id"]
                            else stable_id(
                                "node:legacy_drug" if r["category"] == "DRUG" else "node:device"
                            )
                        )
                        in branch
                    ),
                }
            )
    affected = [p for p in PROFILES if previous["rules"][p] != payload["rules"][p]]
    result = {
        "errors": [
            f"{b['category_name']} · {b['label']}为必填或条件必填，"
            "严格字典没有符合长度 / 格式规则的启用值，请补充字典值或调整规则"
            for b in checks
            if b["blocked"]
        ],
        "dictionary_versions": {key: d["version"] for key, d in dictionaries.items()},
        "impacts": impacts,
        "changed_profiles": affected,
        "products_requiring_revalidation": sum(
            r["count"] for r in counts if r["category"] in {PROFILES[p] for p in affected}
        ),
        "note": "新增必填不回填旧数据；既有商品在下次修订或审核时按新规则验证。",
    }
    result["preview_token"] = sha256(
        json.dumps(
            {"payload": payload, "impact": jsonable_encoder(result)}, sort_keys=True
        ).encode()
    ).hexdigest()
    return result


class MasterConfiguration:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            schema_lock(repo, ctx)
            dictionary_lock(repo, ctx)
            current = current_schema(repo)
            history = (
                repo.rows(
                    (
                        "SELECT "
                        "id,revision,base_revision,state,version,created_at,published_at "
                        "FROM med.catalog_schema ORDER BY created_at DESC LIMIT 50"
                    ),
                    {},
                )
                if "catalog.configure" in ctx.permissions
                else []
            )
            return dict(
                jsonable_encoder(
                    {
                        "revision": current["revision"],
                        "subtypes": SUBTYPES,
                        "role_labels": ROLE_LABELS,
                        "dictionaries": {
                            key: current_dictionary(repo, key) for key in MASTER_DICTIONARY_KEYS
                        },
                        "payload": current["payload"],
                        "history": history,
                    }
                )
            )

    def snapshot(self, ctx: TenantContext, target: UUID) -> dict[str, Any]:
        ctx.require("catalog.configure")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            schema_lock(repo, ctx)
            dictionary_lock(repo, ctx)
            rows = repo.rows("SELECT * FROM med.catalog_schema WHERE id=:id", {"id": target})
            if not rows:
                raise BusinessError("NOT_FOUND", "配置不存在或不可见", 404)
            row = rows[0]
            return dict(
                jsonable_encoder(
                    {
                        **row,
                        "preview": preview(repo, row["payload"], current_schema(repo)["payload"]),
                    }
                )
            )

    def save(self, ctx: TenantContext, body: SchemaSave, key: str) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            schema_lock(repo, ctx)
            dictionary_lock(repo, ctx)
            current = current_schema(repo)
            if current["revision"] != body.base_revision:
                raise BusinessError(
                    "FORM_VERSION_CONFLICT", "发布版本已变化，请保留草稿并重新比较", 409
                )
            errors = configuration_errors(body.payload, current["payload"])
            if errors:
                raise BusinessError("CONFIG_INVALID", "；".join(errors[:12]), 422)
            target = body.draft_id or uuid4()
            params = {
                "t": ctx.tenant_id,
                "id": target,
                "base": body.base_revision,
                "payload": json.dumps(body.payload),
                "actor": ctx.principal_id,
                "version": body.expected_version,
            }
            if body.draft_id:
                rows = repo.rows(
                    (
                        "UPDATE med.catalog_schema SET payload=CAST(:payload AS "
                        "jsonb),version=version+1,updated_by=:actor\n"
                        "                   WHERE id=:id AND state='DRAFT' AND "
                        "version=:version RETURNING version"
                    ),
                    params,
                )
            else:
                rows = repo.rows(
                    (
                        "INSERT INTO med.catalog_schema(tenant_id,id,revision,state,base_r"
                        "evision,payload,created_by,updated_by)\n"
                        "                  VALUES(:t,:id,:base,'DRAFT',:base,CAST(:payload"
                        " AS jsonb),:actor,:actor) RETURNING version"
                    ),
                    params,
                )
            if not rows:
                raise BusinessError("VERSION_CONFLICT", "草稿已变化或已发布，请重新加载", 409)
            audit(
                repo,
                ctx,
                "catalog_schema",
                target,
                "master.schema.draft",
                {"reason": body.reason, "base_revision": body.base_revision},
            )
            return {
                "id": str(target),
                "version": rows[0]["version"],
                "preview": preview(repo, body.payload, current["payload"]),
            }

        return command(
            self.uow,
            ctx,
            "master.schema.save",
            str(body.draft_id or "new"),
            key,
            body.model_dump(mode="json"),
            run,
        )

    def publish(
        self, ctx: TenantContext, target: UUID, body: SchemaPublish, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            schema_lock(repo, ctx)
            dictionary_lock(repo, ctx)
            current = ensure_schema(repo, ctx)
            rows = repo.rows(
                "SELECT * FROM med.catalog_schema WHERE id=:id FOR UPDATE", {"id": target}
            )
            if (
                not rows
                or rows[0]["state"] != "DRAFT"
                or rows[0]["version"] != body.expected_version
                or rows[0]["base_revision"] != current["revision"]
            ):
                raise BusinessError("VERSION_CONFLICT", "配置版本已变化，须重新预览", 409)
            draft = rows[0]
            impact = preview(repo, draft["payload"], current["payload"])
            if impact["errors"]:
                raise BusinessError("CONFIG_INVALID", "；".join(impact["errors"][:12]), 422)
            if impact["preview_token"] != body.preview_token:
                raise BusinessError("PREVIEW_STALE", "影响范围已变化，须重新预览后发布", 409)
            persist_nodes(repo, ctx, draft["payload"])
            revision = current["revision"] + 1
            repo.execute(
                (
                    "UPDATE med.catalog_schema SET state='PUBLISHED',revision=:r,publi"
                    "shed_at=now(),updated_by=:actor,version=version+1 WHERE id=:id"
                ),
                {"r": revision, "id": target, "actor": ctx.principal_id},
            )
            audit(
                repo,
                ctx,
                "catalog_schema",
                target,
                "master.schema.published",
                {"revision": revision, "reason": body.reason, "impact": impact},
            )
            return {"revision": revision, "id": str(target)}

        return command(
            self.uow,
            ctx,
            "master.schema.publish",
            str(target),
            key,
            body.model_dump(mode="json"),
            run,
        )
