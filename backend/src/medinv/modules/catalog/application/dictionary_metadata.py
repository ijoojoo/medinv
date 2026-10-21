"""Navigation and additive identities; legacy exact-text references remain intact."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError

GROUPS = [
    ("product", "商品资料", "▧", "商品建档与经营分类"),
    ("purchase", "采购供应", "↓", "采购办理与供应商管理"),
    ("sales", "销售运营", "↑", "客户与销售管理"),
    ("warehouse", "仓储物流", "⌂", "储存与配送选项"),
    ("quality", "质量合规", "◇", "经营范围、验收与质量台账"),
    ("finance", "财务结算", "¥", "往来结算选项"),
    ("system", "系统与对接", "⚙", "组织资料与通用分类"),
]
GROUP_KEYS = {
    "purchase_approval": "purchase",
    "supplier_grade": "purchase",
    "customer_grade": "sales",
    "storage_conditions": "warehouse",
    "delivery_method": "warehouse",
    "payment_method": "finance",
    "department": "system",
    "staff_status": "system",
    "drug_business_scope": "quality",
    "device_business_scope": "quality",
    "inspection_qualified": "quality",
    "inspection_mixed": "quality",
    "inspection_rejected": "quality",
    "maintenance_type": "quality",
}


def identity(scope: str, key: str, value: str = "") -> str:
    # Length-safe tuple encoding prevents separator collisions; no fuzzy matching.
    import json

    return str(uuid5(NAMESPACE_URL, json.dumps(["medinv-dictionary-v1", scope, key, value])))


def metadata(key: str) -> dict[str, Any]:
    return {
        "dictionary_id": identity("definition", key),
        "group_key": GROUP_KEYS.get(key, "product"),
        "kind": "SYSTEM"
        if key == "staff_status"
        else "TREE"
        if key == "department"
        else "HYBRID"
        if key
        in {
            "dosage_form",
            "base_unit",
            "recommendation_level",
            "abc_class",
            "purchase_approval",
            "inspection_qualified",
            "inspection_mixed",
            "inspection_rejected",
        }
        else "TENANT",
        "hierarchical": key == "department",
        "extendable": key not in {"staff_status", "abc_class"},
        "retention": "已保存值保留身份及名称；停用后历史仍可追溯",
    }


def enrich(
    repo: SqlSession, key: str, items: list[dict[str, Any]], defaults: list[str]
) -> list[dict[str, Any]]:
    tenant = str(repo.rows("SELECT med.current_tenant() AS id", {})[0]["id"])
    result = []
    default_value = next((i["value"] for i in items if i["active"]), None)
    explicit_default = any("is_default" in i for i in items)
    for item in items:
        scope = "system" if item["value"] in defaults else tenant
        value_id = item.get("id") or identity(scope, key, item["value"])
        result.append(
            {
                **item,
                "id": value_id,
                "code": item.get("code") or "V_" + value_id.replace("-", "").upper(),
                "description": item.get("description", ""),
                "parent_id": item.get("parent_id"),
                "origin": "SYSTEM" if item["value"] in defaults else "TENANT",
                "is_default": item.get("is_default", False)
                if explicit_default
                else key
                in {
                    "purchase_approval",
                    "inspection_qualified",
                    "inspection_mixed",
                    "inspection_rejected",
                }
                and item["value"] == default_value,
            }
        )
    return result


def validate_metadata(key: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    if key in {
        "purchase_approval",
        "inspection_qualified",
        "inspection_mixed",
        "inspection_rejected",
    }:
        defaults = [i for i in after if i["is_default"]]
        if len(defaults) != 1 or not defaults[0]["active"]:
            raise BusinessError(
                "DICTIONARY_DEFAULT", "请明确选择一个启用值作为默认结论；排序不改变默认值", 422
            )
    old = {i["value"]: i for i in before}
    for item in after:
        previous = old.get(item["value"])
        if previous and any(item[k] != previous[k] for k in ("id", "code", "origin")):
            raise BusinessError(
                "DICTIONARY_IDENTITY_IMMUTABLE", "已保存值的身份、编码和来源不可修改", 422
            )
    if key == "staff_status" and after != before:
        raise BusinessError("DICTIONARY_PROTECTED", "系统标准值由系统管理，企业不能修改", 422)
    for field in ("id", "code"):
        values = [i[field] for i in after]
        if len(values) != len(set(values)):
            raise BusinessError("DICTIONARY_DUPLICATE", "字典身份或稳定编码重复", 422)
    nodes = {i["id"]: i for i in after}
    for item in after:
        parent = item["parent_id"]
        seen = {item["id"]}
        while parent:
            if not metadata(key)["hierarchical"] or parent not in nodes:
                raise BusinessError("DICTIONARY_PARENT", "上级值必须属于当前分级字典", 422)
            if parent in seen or len(seen) >= 5:
                raise BusinessError("DICTIONARY_CYCLE", "层级不能循环，且最多支持五级", 422)
            seen.add(parent)
            if item["active"] and not nodes[parent]["active"]:
                raise BusinessError(
                    "DICTIONARY_PARENT_INACTIVE", "启用子项之前必须启用全部上级；请先停用子项", 422
                )
            parent = nodes[parent]["parent_id"]


def usage(repo: SqlSession, key: str) -> dict[str, Any]:
    """Aggregate current master references; not a claim to count immutable snapshots."""
    sources = []
    if key in {
        "dosage_form",
        "trade_name",
        "storage_conditions",
        "drug_business_scope",
        "device_business_scope",
        "recommendation_level",
        "abc_class",
    }:
        sources.append(f"SELECT id, {key} AS value, 'product' AS source FROM med.product")
    if key in {"recommendation_level", "abc_class"}:
        sources.append(f"SELECT id, {key} AS value, 'party' AS source FROM med.party")
    if key == "abc_class":
        sources += [
            f"SELECT id,abc_secondary_class AS value,'{t}' AS source FROM med.{t}"
            for t in ("product", "party")
        ]
    if key == "base_unit":
        sources.append("SELECT id,base_unit AS value,'sku' AS source FROM med.sku")
    rows = (
        repo.rows(
            "SELECT value,count(*) AS count FROM (SELECT DISTINCT id,value,source FROM ("
            + " UNION ALL ".join(sources)
            + ") refs) unique_refs GROUP BY value",
            {},
        )
        if sources
        else []
    )
    return {
        "scope": "当前商品、往来单位或规格资料；不含历史快照"
        if sources
        else "暂未提供该字典的业务引用统计",
        "supported": bool(sources),
        "counts": {r["value"]: r["count"] for r in rows},
    }
