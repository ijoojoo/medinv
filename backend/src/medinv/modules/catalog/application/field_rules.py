"""Bounded field policies: no tenant SQL, scripts, or arbitrary regex execution."""

import json
import re
import unicodedata
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.field_rules import Category, FieldRule, FieldRulesUpdate
from medinv.modules.catalog.application.dictionaries import (
    DEFINITIONS,
    current_dictionary,
    dictionary_lock,
    validate_dictionary,
)
from medinv.modules.catalog.application.dictionary_rules import (
    dictionary_check,
    reject_dictionary_conflicts,
)
from medinv.modules.catalog.application.field_policy import (
    DEVICE_SOURCE,
    SELECTORS,
    current,
    definitions,
    field_label,
    policy_lock,
)
from medinv.modules.catalog.domain.field_catalog import template
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def validate_configuration(category: str, fields: list[FieldRule]) -> None:
    known = {d["key"]: d for d in definitions(category)}
    if len(fields) != len(known) or {r.key for r in fields} != set(known):
        raise BusinessError("FIELD_RULE_INVALID", "字段清单不完整、重复或不属于当前商品类别", 422)
    selector, options = SELECTORS[category]

    # Reserve every system label, including conditional variants, so aliases cannot
    # impersonate a different field even when that field is hidden or renamed.
    def normalized(label: str) -> str:
        return "".join(unicodedata.normalize("NFKC", label).casefold().split())

    names = {
        normalized(label): "identity"
        for label in ("商品类别", "商品编码", "基本单位", "数量小数位")
    }
    for definition in known.values():
        for label in (definition["label"], *(c["label"] for c in definition["label_when"])):
            names[normalized(label)] = definition["key"]
    for rule in fields:
        d = known[rule.key]
        if rule.display_name:
            if not d["alias_allowed"]:
                raise BusinessError(
                    "FIELD_RULE_INVALID", f"{d['label']}名称由分类规则控制，不能自定义", 422
                )
            label_key = normalized(rule.display_name)
            if label_key in names and names[label_key] != rule.key:
                raise BusinessError(
                    "FIELD_RULE_INVALID", f"显示名称“{rule.display_name}”与其他字段重名", 422
                )
            names[label_key] = rule.key
        if d["locked_required"] and (not rule.required or not rule.visible):
            raise BusinessError(
                "FIELD_RULE_INVALID", f"{d['label']}是基础必填字段，不能隐藏或取消必填", 422
            )
        if rule.key == selector and (not rule.visible or rule.required_when):
            raise BusinessError("FIELD_RULE_INVALID", "分类字段必须显示，且不能依赖自身联动", 422)
        if rule.required and not rule.visible:
            raise BusinessError("FIELD_RULE_INVALID", f"{d['label']}设为必填后必须显示", 422)
        if not 0 <= rule.min_length <= rule.max_length <= d["capacity"]:
            raise BusinessError("FIELD_RULE_INVALID", f"{d['label']}长度范围超出字段容量", 422)
        if d["kind"] != "text" and rule.validator != "NONE":
            raise BusinessError("FIELD_RULE_INVALID", "文本验证规则不能用于日期或分类字段", 422)
        if d["kind"] == "text" and rule.max_length == 0:
            raise BusinessError("FIELD_RULE_INVALID", "文本最大长度必须大于零", 422)
        conditions = [(c.field, c.equals) for c in rule.required_when]
        if len(conditions) != len(set(conditions)) or any(
            field != selector or value not in options for field, value in conditions
        ):
            raise BusinessError("FIELD_RULE_INVALID", "联动条件重复或不属于当前商品类别", 422)


def validate_product(
    repo: SqlSession,
    ctx: TenantContext,
    values: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    only: set[str] | frozenset[str] | None = None,
) -> str:
    category = str(values["category"])
    policy_lock(repo, ctx, category)
    version, fields = current(repo, category)
    known = {d["key"]: d for d in definitions(category)}
    errors = []
    for rule in sorted(fields, key=lambda r: (r.order, r.key)):
        if only is not None and rule.key not in only:
            continue
        d = known[rule.key]
        label = field_label(d, rule, values)
        value = values.get(rule.key)
        text = (
            ""
            if value is None or (d["kind"] == "select" and value == "UNKNOWN")
            else str(value).strip()
        )
        required = (
            d["locked_required"]
            or rule.required
            or any(values.get(c.field) == c.equals for c in rule.required_when)
        )
        if not text:
            if required:
                errors.append(f"{label}必填")
            continue
        # Hidden fields retain their values and remain subject to validation.
        if d["kind"] == "text":
            if rule.key in DEFINITIONS:
                validate_dictionary(
                    repo, ctx, rule.key, text, (previous or {}).get(rule.key), label=label
                )
            if not rule.min_length <= len(text) <= rule.max_length:
                errors.append(f"{label}长度应为 {rule.min_length}–{rule.max_length} 个字符")
            invalid = (
                (rule.validator == "NO_WHITESPACE" and any(c.isspace() for c in text))
                or (rule.validator == "ASCII_CODE" and not re.fullmatch(r"[A-Za-z0-9_.\-/]+", text))
                or (rule.validator == "DIGITS" and not re.fullmatch(r"[0-9]+", text))
            )
            if invalid:
                errors.append(f"{label}不符合已配置的格式规则")
    if errors:
        raise BusinessError("PRODUCT_FIELD_INVALID", "；".join(errors), 422)
    return version


class ProductFieldRules:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, category: Category) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            policy_lock(repo, ctx, category)
            dictionary_lock(repo, ctx)
            version, fields = current(repo, category)
            known = {d["key"]: d for d in definitions(category)}
            history = (
                repo.rows(
                    """SELECT e.created_at,e.created_by,e.reason
                FROM med.audit_event e JOIN med.product_field_rules p ON
                (p.tenant_id,p.id)=(e.tenant_id,e.resource_id)
                WHERE p.category=:c AND e.resource_type='product_field_rules'
                ORDER BY e.created_at DESC,e.id DESC LIMIT 10""",
                    {"c": category},
                )
                if "catalog.configure" in ctx.permissions
                else []
            )
            return dict(
                jsonable_encoder(
                    {
                        "category": category,
                        "template": template(category),
                        "version": version,
                        "unit_dictionary": current_dictionary(repo, "base_unit"),
                        "fields": [
                            {
                                **known[r.key],
                                **r.model_dump(),
                                "dictionary": current_dictionary(repo, r.key)
                                if r.key in DEFINITIONS
                                else None,
                            }
                            for r in sorted(fields, key=lambda r: (r.order, r.key))
                        ],
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in history],
                        "legal_source": DEVICE_SOURCE if category == "DEVICE" else None,
                    }
                )
            )

    def update(
        self, ctx: TenantContext, category: Category, body: FieldRulesUpdate, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            policy_lock(repo, ctx, category, write=True)
            version, before = current(repo, category)
            if version != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "字段设置已被修改，请重新加载后保存", 409)
            previous = {r.key: r for r in before}
            # An older client does not know display_name; omission preserves it.
            fields = [
                r.model_copy(update={"display_name": previous[r.key].display_name})
                if "display_name" not in r.model_fields_set and r.key in previous
                else r
                for r in body.fields
            ]
            validate_configuration(category, fields)
            after = [r.model_dump(mode="json") for r in sorted(fields, key=lambda r: r.key)]
            old = [r.model_dump(mode="json") for r in sorted(before, key=lambda r: r.key)]
            if old == after:
                raise BusinessError("FIELD_RULE_UNCHANGED", "字段设置没有变化", 422)
            dictionary_lock(repo, ctx)
            known = {d["key"]: d for d in definitions(category)}
            reject_dictionary_conflicts(
                [
                    dictionary_check(category, known[r.key], r, current_dictionary(repo, r.key))
                    for r in fields
                    if r.key in DEFINITIONS
                ]
            )
            row = repo.rows(
                """INSERT INTO med.product_field_rules
                (tenant_id,id,category,fields,created_by,updated_by)
                VALUES(:t,:id,:c,CAST(:fields AS jsonb),:actor,:actor)
                ON CONFLICT(tenant_id,category) DO UPDATE SET fields=EXCLUDED.fields,
                version=product_field_rules.version+1,updated_by=:actor,updated_at=clock_timestamp()
                RETURNING id,version""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "c": category,
                    "fields": json.dumps(after, ensure_ascii=False),
                    "actor": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "product_field_rules",
                row["id"],
                "catalog.field_rules.updated",
                {
                    "category": category,
                    "before": old,
                    "after": after,
                    "previous_version": version,
                    "version": str(row["version"]),
                    "reason": body.reason,
                },
            )
            return {"category": category, "version": str(row["version"])}

        payload = body.model_dump(mode="json")
        for source, item in zip(body.fields, payload["fields"], strict=True):
            if "display_name" not in source.model_fields_set:
                item.pop("display_name", None)
        return command(
            self.uow,
            ctx,
            "catalog.field_rules.update",
            category,
            key,
            payload,
            run,
        )
