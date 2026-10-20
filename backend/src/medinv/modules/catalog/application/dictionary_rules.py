"""Compatibility between field policies and dictionary choices, without mutating stored values."""

import re
from typing import Any

from medinv.modules.catalog.api.field_rules import FieldRule
from medinv.modules.catalog.application.field_policy import current, definitions, policy_lock
from medinv.modules.catalog.domain.master_schema import NODES, PROFILES, presentation
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

BOUND_CATEGORIES = {
    "drug_business_scope": ("DRUG",),
    "device_business_scope": ("DEVICE",),
    "dosage_form": ("DRUG",),
    "trade_name": ("DEVICE", "DRUG"),
    "storage_conditions": ("DEVICE", "DRUG"),
}
CATEGORY_NAMES = {"DEVICE": "医疗器械", "DRUG": "药品", "BUSINESS": "商品、供应商与客户"}
MASTER_DICTIONARY_KEYS = (
    "base_unit",
    "dosage_form",
    "trade_name",
    "storage_conditions",
    "drug_business_scope",
    "device_business_scope",
)


def master_dictionary_bindings(
    schema: dict[str, Any], key: str, dictionary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bind by immutable field key; aliases never create or merge dictionaries."""
    if key not in MASTER_DICTIONARY_KEYS:
        return []
    result = []
    names = {p: name for code, name, _, p in NODES if code == p}
    for profile in PROFILES:
        f = next(f for f in presentation(schema, profile, {}) if f["key"] == key)
        if not f["applicable"] or f["lifecycle"] == "INACTIVE":
            continue
        rule = FieldRule(
            key=key,
            order=0,
            required=f["required"],
            min_length=f.get("min_length", 0),
            max_length=min(f.get("max_length") or 500, dictionary["capacity"]),
            validator=f.get("validator", "NONE"),
        )
        check = dictionary_check(
            "BUSINESS", {"locked_required": False, "label": f["label"]}, rule, dictionary
        )
        # MDM rejects placeholder prose, including when it occurs in a dictionary.
        invalid = [
            i["value"]
            for i in dictionary["items"]
            if i["active"]
            and (i["value"] in {"—", "未知", "不适用"} or not text_matches(i["value"], rule))
        ]
        required = bool(f["required"] or f.get("required_when"))
        result.append(
            {
                **check,
                "category": f"MDM:{profile}",
                "category_name": names[profile],
                "source": "MDM",
                "profile": profile,
                "reject_placeholders": True,
                "required": required,
                "required_when": [f["required_when"]] if f.get("required_when") else [],
                "incompatible_values": invalid,
                "eligible_count": check["active_count"] - len(invalid),
                "blocked": required
                and dictionary["mode"] == "STRICT"
                and check["active_count"] == len(invalid),
            }
        )
    return result


def text_matches(value: str, rule: FieldRule) -> bool:
    if not rule.min_length <= len(value) <= rule.max_length:
        return False
    return not (
        (rule.validator == "NO_WHITESPACE" and any(c.isspace() for c in value))
        or (rule.validator == "ASCII_CODE" and not re.fullmatch(r"[A-Za-z0-9_.\-/]+", value))
        or (rule.validator == "DIGITS" and not re.fullmatch(r"[0-9]+", value))
    )


def dictionary_check(
    category: str, definition: dict[str, Any], rule: FieldRule, dictionary: dict[str, Any]
) -> dict[str, Any]:
    active = [i["value"] for i in dictionary["items"] if i["active"]]
    invalid = [v for v in active if not text_matches(v, rule)]
    eligible = len(active) - len(invalid)
    required = bool(definition["locked_required"] or rule.required or rule.required_when)
    return {
        "category": category,
        "category_name": CATEGORY_NAMES[category],
        "field": rule.key,
        "label": rule.display_name or definition["label"],
        "required": required,
        "required_when": [c.model_dump() for c in rule.required_when],
        "min_length": rule.min_length,
        "max_length": rule.max_length,
        "validator": rule.validator,
        "active_count": len(active),
        "eligible_count": eligible,
        "incompatible_values": invalid,
        "blocked": required and dictionary["mode"] == "STRICT" and eligible == 0,
    }


def lock_dictionary_bindings(repo: SqlSession, ctx: TenantContext, key: str) -> None:
    # Match product writes: schema -> legacy policies -> dictionary. Never reverse it.
    if key in MASTER_DICTIONARY_KEYS:
        from medinv.modules.catalog.application.master_config import schema_lock

        schema_lock(repo, ctx)
    for category in BOUND_CATEGORIES.get(key, ()):
        policy_lock(repo, ctx, category)


def dictionary_bindings(
    repo: SqlSession, key: str, dictionary: dict[str, Any]
) -> list[dict[str, Any]]:
    if key in {
        "purchase_approval",
        "inspection_qualified",
        "inspection_mixed",
        "inspection_rejected",
    }:
        return []
    result = []
    for category in BOUND_CATEGORIES.get(key, ()):
        _, fields = current(repo, category)
        definition = next(d for d in definitions(category) if d["key"] == key)
        rule = next(r for r in fields if r.key == key)
        result.append(dictionary_check(category, definition, rule, dictionary))
    if not result:
        field_keys = [key, "abc_secondary_class"] if key == "abc_class" else [key]
        for field in field_keys:
            rule = FieldRule(
                key=field, order=0, required=key == "base_unit", max_length=dictionary["capacity"]
            )
            result.append(
                dictionary_check(
                    "BUSINESS" if key != "base_unit" else "DEVICE",
                    {"locked_required": key == "base_unit", "label": dictionary["name"]},
                    rule,
                    dictionary,
                )
            )
            if key == "base_unit":
                result.append(
                    dictionary_check(
                        "DRUG",
                        {"locked_required": True, "label": dictionary["name"]},
                        rule,
                        dictionary,
                    )
                )
    if key in MASTER_DICTIONARY_KEYS:
        from medinv.modules.catalog.application.master_config import current_schema

        result.extend(master_dictionary_bindings(current_schema(repo)["payload"], key, dictionary))
    return result


def reject_dictionary_conflicts(bindings: list[dict[str, Any]]) -> None:
    conflicts = [f"{b['category_name']} · {b['label']}" for b in bindings if b["blocked"]]
    if conflicts:
        raise BusinessError(
            "DICTIONARY_RULE_CONFLICT",
            "；".join(conflicts)
            + "为必填或条件必填，但严格字典没有符合长度 / 格式规则的启用值。请补充可用值或调整规则",
            422,
        )
