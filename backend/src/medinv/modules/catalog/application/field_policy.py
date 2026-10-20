from typing import Any

from medinv.modules.catalog.api.field_rules import FieldRule
from medinv.modules.catalog.domain.field_catalog import metadata, template
from medinv.platform.database.sql import SqlSession
from medinv.platform.tenancy.context import TenantContext

DEVICE_SOURCE = (
    "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
    "art_70607fc4160041a383e68ff6bfb2826f.html"
)
SELECTORS = {
    "DEVICE": (
        "device_class",
        {"UNKNOWN": "待核定", "I": "第一类", "II": "第二类", "III": "第三类"},
    ),
    "DRUG": (
        "drug_sale_class",
        {"UNKNOWN": "待核定（不可零售）", "OTC": "非处方药 OTC", "RX": "处方药 RX"},
    ),
}


def definitions(category: str) -> list[dict[str, Any]]:
    # Names, types, capacities and statutory semantics are system-owned.
    common = [
        ("name", "名称", 200, True),
        ("manufacturer_name", "生产企业", 100, True),
        ("specification", "规格型号", 200, True),
        (
            "approval_number",
            "批准编号" if category == "DRUG" else "批准 / 注册 / 备案编号",
            120,
            True,
        ),
        ("valid_from", "资质生效日期", 0, True),
        ("valid_until", "资质截止日期", 0, True),
        ("search_alias", "检索别名 / 助记码", 200, False),
        ("trade_name", "商品名 / 品牌", 100, False),
        ("manufacturer_address", "生产企业地址", 300, False),
        ("storage_conditions", "储存条件（按标签 / 说明书）", 500, False),
        ("quality_note", "质量管控备注", 500, False),
    ]
    specific = (
        [
            ("drug_business_scope", "商品经营范围类别（药品）", 100, False),
            ("generic_name", "药品通用名", 200, False),
            ("dosage_form", "剂型", 100, False),
            ("authorization_holder", "上市许可持有人", 200, False),
            ("authorization_holder_address", "上市许可持有人地址", 300, False),
        ]
        if category == "DRUG"
        else [
            ("device_business_scope", "商品经营范围类别（器械）", 100, False),
            ("device_model", "器械型号", 200, False),
            ("registrant_name", "注册人 / 备案人", 200, False),
            ("registrant_address", "注册人 / 备案人地址", 300, False),
        ]
    )
    selector, options = SELECTORS[category]
    entries = [
        common[0],
        (selector, "器械管理类别" if category == "DEVICE" else "药品销售分类", 0, False),
        *common[1:6],
        *specific,
        *common[6:],
    ]
    return [
        {
            **metadata(key),
            "key": key,
            "label": label,
            "capacity": capacity,
            "locked_required": locked,
            "kind": "select" if key == selector else "date" if key.startswith("valid_") else "text",
            "options": options if key == selector else {},
            "label_when": [
                {"field": "device_class", "equals": value, "label": label_text}
                for value, label_text in [
                    ("I", "产品备案号"),
                    ("II", "注册证号"),
                    ("III", "注册证号"),
                ]
            ]
            if category == "DEVICE" and key == "approval_number"
            else [
                {
                    "field": "device_class",
                    "equals": value,
                    "label": ("备案人" if value == "I" else "注册人")
                    + ("地址" if key.endswith("address") else ""),
                }
                for value in ("I", "II", "III")
            ]
            if category == "DEVICE" and key in ("registrant_name", "registrant_address")
            else [],
        }
        for key, label, capacity, locked in entries
    ]


def defaults(category: str) -> list[FieldRule]:
    return [
        FieldRule(
            key=d["key"],
            order=(i + 1) * 10,
            required=d["locked_required"],
            max_length=d["capacity"],
        )
        for i, d in enumerate(definitions(category))
    ]


def policy_lock(
    repo: SqlSession, ctx: TenantContext, category: str, *, write: bool = False
) -> None:
    # Shared readers also lock the absent/default policy, so first-save cannot race validation.
    function = "pg_advisory_xact_lock" if write else "pg_advisory_xact_lock_shared"
    repo.execute(
        f"SELECT {function}(hashtextextended(:scope,0))",
        {"scope": f"product-field-rules:{ctx.tenant_id}:{category}"},
    )


def current(repo: SqlSession, category: str) -> tuple[str, list[FieldRule]]:
    rows = repo.rows(
        "SELECT version,fields FROM med.product_field_rules WHERE category=:c", {"c": category}
    )
    if not rows:
        return "0", defaults(category)
    saved = {r["key"]: FieldRule(**r) for r in rows[0]["fields"]}
    # New built-in fields receive defaults without rewriting existing category policies.
    return str(rows[0]["version"]), [saved.get(r.key, r) for r in defaults(category)]


def field_label(definition: dict[str, Any], rule: FieldRule, values: dict[str, Any]) -> str:
    return str(
        next(
            (
                c["label"]
                for c in definition.get("label_when", [])
                if values.get(c["field"]) == c["equals"]
            ),
            rule.display_name or definition["label"],
        )
    )


def record_policy(repo: SqlSession, ctx: TenantContext, values: dict[str, Any]) -> dict[str, Any]:
    category = values["category"]
    policy_lock(repo, ctx, category)
    version, rules = current(repo, category)
    known = {d["key"]: d for d in definitions(category)}
    return {
        "category": category,
        "version": version,
        "template": template(category, values),
        "context": {SELECTORS[category][0]: values.get(SELECTORS[category][0], "UNKNOWN")},
        "fields": [{**known[r.key], **r.model_dump()} for r in rules],
    }
