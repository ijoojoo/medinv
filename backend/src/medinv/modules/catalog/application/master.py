"""Aggregate adapter over the existing product identity; shared by forms and CSV import."""

import csv
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.master import MasterImport, MasterSave, OrganizationCreate
from medinv.modules.catalog.application.code_rules import resolve_product_codes
from medinv.modules.catalog.application.dictionaries import DEFINITIONS as DICTIONARIES
from medinv.modules.catalog.application.dictionaries import validate_dictionary
from medinv.modules.catalog.application.master_config import (
    business_rules_changed,
    current_schema,
    ensure_schema,
    schema_lock,
)
from medinv.modules.catalog.application.master_projection import related_records
from medinv.modules.catalog.application.master_projection import values_of as values_of
from medinv.modules.catalog.application.master_references import inline_text, resolve_references
from medinv.modules.catalog.application.master_trade import check_trade_fields, write_trade_fields
from medinv.modules.catalog.domain.master_schema import (
    DETAIL_FIELDS,
    FIELDS,
    PROFILES,
    descendants,
    node_path,
    presentation,
    regulatory_role,
    stable_id,
)
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

PRODUCT_FIELDS = [f["key"] for f in FIELDS if f["binding_key"].startswith("product.")]
RELATIONS = {"regulatory_subject", "manufacturer", "service_provider", "product_certificates"}
EXTERNAL = {"barcodes", "reference_prices", "operational_settings", "external_codes"}
CERT_TYPES = {
    "DRUG_MAH": "DRUG_APPROVAL",
    "DEVICE_FILER": "DEVICE_FILING",
    "DEVICE_REGISTRANT": "DEVICE_REGISTRATION",
    "HEALTH_REGISTRANT": "HEALTH_REGISTRATION",
    "HEALTH_FILER": "HEALTH_FILING",
    "FSMP_REGISTRANT": "FSMP_REGISTRATION",
    "INFANT_REGISTRANT": "INFANT_FORMULA_REGISTRATION",
    "COSMETIC_FILER": "COSMETIC_FILING",
    "COSMETIC_REGISTRANT": "COSMETIC_REGISTRATION",
}


def raw_record(repo: SqlSession, target: UUID, lock: bool = False) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.product WHERE id=:id" + (" FOR UPDATE" if lock else ""), {"id": target}
    )
    if not rows:
        raise BusinessError("NOT_FOUND", "商品不存在或不可见", 404)
    return rows[0]


def category_node(
    schema: dict[str, Any], identity: str, *, creating: bool = False
) -> dict[str, Any]:
    nodes = {n["node_id"]: n for n in schema["nodes"]}
    node = nodes.get(identity)
    if not node or not node["field_profile_id"]:
        raise BusinessError("CATEGORY_REQUIRED", "请明确选择可建档品类，分组不能直接保存商品", 422)
    if creating:
        current = node
        if not node["is_assignable"]:
            raise BusinessError("CATEGORY_REQUIRED", "请选择该分支内允许建档的品类", 422)
        while current:
            if not current["enabled"]:
                raise BusinessError("CATEGORY_DISABLED", "所选品类或其祖先已停用，不能新建", 422)
            current = nodes.get(current["parent_id"])
    return dict(node)


def actual_node(row: dict[str, Any]) -> str:
    return (
        str(row["category_node_id"])
        if row.get("category_node_id")
        else stable_id("node:" + ("legacy_drug" if row["category"] == "DRUG" else "device"))
    )


def validation(
    repo: SqlSession,
    ctx: TenantContext,
    schema: dict[str, Any],
    profile: str,
    values: dict[str, Any],
    submitted: dict[str, Any],
    previous: dict[str, Any] | None,
    formal: bool,
    product_id: UUID | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    definitions = {f["key"]: f for f in presentation(schema, profile, values)}

    def error(key: str, message: str) -> None:
        slot: dict[str, Any] = next((s for s in schema["slots"] if s["field_key"] == key), {})
        issues.append(
            {
                "field": key,
                "tab": slot.get("tab", "basic"),
                "slot_id": slot.get("slot_id", ""),
                "message": message,
            }
        )

    for key in submitted:
        if key not in definitions:
            error(key, "未知字段，不能作为扩展值写入")
    for key, f in definitions.items():
        value = values.get(key)
        changed = key in submitted and (previous is None or value != previous.get(key))
        if changed and (
            f["read_permission"] not in ctx.permissions
            or f["write_permission"] not in ctx.permissions
            or f["read_only"]
            or f["lifecycle"] == "INACTIVE"
        ):
            error(key, f["label"] + "无修改权限或已停用")
            continue
        if key in EXTERNAL:
            if changed:
                error(key, "请从对应的唯一维护入口修改关联资料")
            continue
        if not f["applicable"]:
            if changed and value not in (None, "", []):
                error(key, f["label"] + "不适用于当前品类 / 子类型")
            continue
        empty = value is None or value == "" or value == []
        if empty:
            if formal and f["required"]:
                error(key, f["label"] + "不能为空")
            continue
        kind = f["value_type"]
        if kind in {"text", "select"}:
            max_length = min(
                f.get("max_length") or 500,
                {
                    "code": 60,
                    "base_unit": 30,
                    "trade_name": 100,
                    "dosage_form": 100,
                    "drug_business_scope": 100,
                    "device_business_scope": 100,
                    "manufacturer_address": 300,
                    "generic_name": 200,
                    "device_model": 200,
                    "name": 200,
                    "search_alias": 200,
                    "specification": 200,
                }.get(key, 500),
            )
            if (
                not isinstance(value, str)
                or not f.get("min_length", 0) <= len(value) <= max_length
                or value in {"—", "未知", "不适用"}
            ):
                error(key, f["label"] + "文本类型或长度无效，不能用占位文案代替真实值")
            elif kind == "select" and value not in {o["value"] for o in f["options"]}:
                error(key, f["label"] + "不在合法选项范围内")
            elif isinstance(value, str):
                validator = f.get("validator", "NONE")
                if (
                    (validator == "NO_WHITESPACE" and any(c.isspace() for c in value))
                    or (validator == "ASCII_CODE" and not re.fullmatch(r"[A-Za-z0-9_.\-/]+", value))
                    or (validator == "DIGITS" and not re.fullmatch(r"[0-9]+", value))
                ):
                    error(key, f["label"] + "不符合已发布格式规则")
                if key in DICTIONARIES and (changed or formal):
                    try:
                        validate_dictionary(repo, ctx, key, value, (previous or {}).get(key))
                    except BusinessError as exc:
                        error(key, str(exc))
        elif kind in {"integer", "decimal"}:
            try:
                if isinstance(value, bool) or isinstance(value, (dict, list)):
                    raise ValueError()
                decimal = Decimal(str(value))
                if (
                    not decimal.is_finite()
                    or abs(decimal) >= Decimal("100000000000")
                    or int(decimal.as_tuple().exponent) < -6
                ):
                    raise ValueError()
                if kind == "integer" and (decimal != decimal.to_integral_value() or decimal <= 0):
                    raise ValueError()
                if kind == "integer" and decimal > 2147483647:
                    raise ValueError()
                if key in {"temperature_min", "temperature_max", "humidity_max"} and (
                    abs(decimal) >= 1000000 or int(decimal.as_tuple().exponent) < -4
                ):
                    raise ValueError()
                if key in {"service_price", "humidity_max"} and decimal < 0:
                    raise ValueError()
                if key == "humidity_max" and decimal > 100:
                    raise ValueError()
            except ValueError, InvalidOperation, TypeError:
                error(key, f["label"] + "数值或精度无效")
        elif kind == "boolean" and type(value) is not bool:
            error(key, f["label"] + "须为布尔值")
        elif kind == "date":
            try:
                date.fromisoformat(str(value))
            except ValueError, TypeError:
                error(key, f["label"] + "日期无效")
        elif kind == "reference":
            try:
                identifier = UUID(str(value))
            except ValueError:
                error(key, "科室身份格式无效")
                continue
            departments = repo.rows(
                (
                    "SELECT * FROM med.catalog_organization WHERE id=:id AND "
                    "kind='DEPARTMENT' AND state='ACTIVE'"
                ),
                {"id": identifier},
            )
            providers = {
                str(r.get("organization_id"))
                for r in values.get("service_provider", [])
                if isinstance(r, dict)
            }
            if not departments or str(departments[0]["parent_id"]) not in providers:
                error(key, "科室须属于所选服务提供机构")
        elif kind == "collection" and (not isinstance(value, list) or len(value) > 100):
            error(key, f["label"] + "须为最多 100 条的关系集合")
    low, high = values.get("temperature_min"), values.get("temperature_max")
    if low not in (None, "") and high not in (None, ""):
        try:
            if Decimal(str(low)) > Decimal(str(high)):
                error("temperature_max", "温度上限不能小于下限")
        except InvalidOperation:
            pass
    role = regulatory_role(profile, values)
    from medinv.modules.catalog.application.qualification_rules import settings

    qualification = settings(repo)
    if (
        formal
        and role
        and qualification["product_regulatory_subject_required"]
        and not values.get("regulatory_subject")
    ):
        error("regulatory_subject", "请明确关联当前监管主体的真实组织身份")
    for field, allowed in [
        ("regulatory_subject", {role} if role else set()),
        ("manufacturer", {"MANUFACTURER", "CONTRACT_MANUFACTURER"}),
        ("service_provider", {"SERVICE_PROVIDER"}),
    ]:
        entries = values.get(field) or []
        if not isinstance(entries, list):
            continue
        seen = set()
        for relation in entries:
            if isinstance(relation, dict) and not relation.get("organization_id"):
                name = relation.get("organization_name")
                if not inline_text(name, 200) or relation.get("role_code") not in allowed:
                    error(field, "请输入有效的企业名称，并明确对应监管类别 / 实际角色")
                elif formal:
                    error(field, "请先保存商品，系统将自动匹配或添加组织字典")
                evidence = relation.get("evidence_ref", "")
                if (
                    not isinstance(evidence, str)
                    or len(evidence) > 120
                    or relation.get("source_certificate_id")
                ):
                    error(field, "新输入的组织请先保存，再补充证件来源关系")
                continue
            try:
                org = UUID(str(relation["organization_id"]))
                r = relation["role_code"]
                evidence = relation.get("evidence_ref", "")
                if (
                    r not in allowed
                    or (org, r) in seen
                    or not isinstance(evidence, str)
                    or len(evidence) > 120
                ):
                    raise ValueError()
                seen.add((org, r))
            except ValueError, TypeError, KeyError:
                error(field, "组织身份或实际角色无效；监管分类变更后须明确更换角色关系")
                continue
            if not repo.rows(
                (
                    "SELECT id FROM med.catalog_organization WHERE id=:id AND "
                    "kind='ORGANIZATION' AND state='ACTIVE'"
                ),
                {"id": org},
            ):
                error(field, "组织不可见、不存在或已停用")
            source = relation.get("source_certificate_id")
            if source:
                linked = any(
                    str(c.get("certificate_id")) == str(source)
                    and c.get("certificate_version") == relation.get("source_certificate_version")
                    for c in (values.get("product_certificates") or [])
                    if isinstance(c, dict)
                )
                if not linked:
                    error(field, "组织关系引用的证件版本须明确关联到本商品")
    certificates = values.get("product_certificates") or []
    from medinv.modules.catalog.application.qualification_rules import (
        settings as qualification_settings,
    )

    qualification = qualification_settings(repo)
    if isinstance(certificates, list):
        expected = CERT_TYPES.get(role or "")
        primary = [c for c in certificates if isinstance(c, dict) and c.get("business_use") is True]
        if len(primary) > 1 or any(
            isinstance(c, dict) and type(c.get("business_use", False)) is not bool
            for c in certificates
        ):
            error("product_certificates", "业务适用证件须明确选择一份，不能指定多个默认证件")
        if (
            formal
            and qualification["product_certificate_required"]
            and expected
            and len(primary) != 1
        ):
            error("product_certificates", "请在证件关系中明确指定本商品的业务适用证件")
        seen_certs = set()
        for link in certificates:
            if isinstance(link, dict) and not link.get("certificate_id"):
                if not expected or not inline_text(link.get("number"), 120):
                    error(
                        "product_certificates", "请输入有效证件编号，并先明确监管子类型 / 器械类别"
                    )
                elif formal:
                    error("product_certificates", "请先保存商品以关联证件，证件随本商品一并审核")
                continue
            try:
                cid = UUID(str(link["certificate_id"]))
                version = int(link["certificate_version"])
                if cid in seen_certs:
                    raise ValueError()
                seen_certs.add(cid)
            except ValueError, TypeError, KeyError:
                error("product_certificates", "证件身份重复或版本无效")
                continue
            rows = repo.rows("SELECT * FROM med.product_certificate WHERE id=:id", {"id": cid})
            if product_id:
                from medinv.modules.catalog.application.change_bundle import certificate_rows

                rows = [
                    c for c in certificate_rows(repo, "product", product_id, rows) if c["id"] == cid
                ]
            if not rows or rows[0]["version"] != version:
                error("product_certificates", "证件不可见或已修订，请重新选择明确版本")
                continue
            certificate = rows[0]
            if (
                qualification["product_certificate_type_check"]
                and certificate["certificate_type"] != expected
            ):
                error("product_certificates", "证件类型与当前监管子类型 / 器械类别不一致")
            if formal and (
                certificate["state"] != "VERIFIED"
                or (
                    qualification["product_certificate_dates"]
                    and certificate["validity_mode"] == "UNCONFIRMED"
                )
                or (
                    qualification["product_certificate_dates"]
                    and certificate["valid_until"]
                    and certificate["valid_until"] < date.today()
                )
                or (
                    qualification["product_certificate_dates"]
                    and certificate["valid_from"]
                    and certificate["valid_from"] > date.today()
                )
            ):
                error("product_certificates", "证件未随主体确认、已停用或不在适用期内")
        if (
            formal
            and qualification["product_certificate_required"]
            and expected
            and not certificates
        ):
            error("product_certificates", "当前监管类别须明确关联适用产品证件")
    return issues


def safe_values(
    ctx: TenantContext, schema: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    return {
        f["key"]: values.get(f["key"])
        for f in schema["fields"]
        if f["read_permission"] in ctx.permissions
    }


class ProductMaster:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def detail(self, ctx: TenantContext, target: UUID) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            current = current_schema(repo)
            row = raw_record(repo, target)
            schema = current["payload"]
            node = category_node(schema, actual_node(row))
            profile = node["field_profile_id"]
            values = values_of(repo, row)
            from medinv.modules.catalog.application.change_bundle import (
                pending,
                product_draft_values,
            )

            draft = pending(repo, "product", target)
            active_values = dict(values)
            values = product_draft_values(repo, target, values)
            history = repo.rows(
                (
                    "SELECT "
                    "data_version,form_revision,category_path,created_at,created_by "
                    "FROM med.product_master_revision WHERE product_id=:p ORDER BY "
                    "data_version DESC LIMIT 50"
                ),
                {"p": target},
            )
            skus = repo.rows(
                (
                    "SELECT id,code,base_unit,qty_scale FROM med.sku WHERE "
                    "product_id=:p ORDER BY code,id"
                ),
                {"p": target},
            )
            return dict(
                jsonable_encoder(
                    {
                        "id": str(target),
                        "version": row["version"],
                        "data_version": row["data_version"],
                        "form_revision": current["revision"],
                        "saved_form_revision": row["form_revision"],
                        "category_node_id": actual_node(row),
                        "category_path": node_path(schema["nodes"], actual_node(row)),
                        "profile": profile,
                        "approval_state": row["approval_state"],
                        "values": safe_values(ctx, schema, values),
                        "active_values": safe_values(ctx, schema, active_values),
                        "has_changes": bool(draft),
                        "skus": skus,
                        "issues": validation(
                            repo, ctx, schema, profile, values, {}, values, True, target
                        ),
                        "history": history,
                        "legacy": {
                            "manufacturer_name": row["manufacturer_name"],
                            "authorization_holder": row["authorization_holder"],
                            "registrant_name": row["registrant_name"],
                            "approval_number": row["approval_number"],
                            "valid_from": row["valid_from"],
                            "valid_until": row["valid_until"],
                            "message": (
                                "旧档原始记载，不自动认定组织角色或证件类型；保留既有业务有效期。"
                            ),
                        },
                    }
                )
            )

    def history(self, ctx: TenantContext, target: UUID, version: int) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                "SELECT * FROM med.product_master_revision WHERE product_id=:p AND data_version=:v",
                {"p": target, "v": version},
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "历史版本不可见或不存在", 404)
            row = rows[0]
            current = current_schema(repo)
            row["snapshot"] = safe_values(ctx, current["payload"], row["snapshot"])
            historical = repo.rows(
                "SELECT payload FROM med.catalog_schema WHERE state='PUBLISHED' AND revision=:v",
                {"v": row["form_revision"]},
            )
            row["field_labels"] = {
                f["key"]: f["default_label"]
                for f in (historical[0]["payload"] if historical else current["payload"])["fields"]
            }
            return dict(jsonable_encoder(row))

    def validate(self, ctx: TenantContext, body: MasterSave) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            current = current_schema(repo)
            if current["revision"] != body.form_revision:
                raise BusinessError(
                    "FORM_VERSION_CONFLICT", "配置已发布新版本，请保留输入并重新加载配置后校验", 409
                )
            previous = (
                values_of(repo, raw_record(repo, body.product_id)) if body.product_id else None
            )
            if body.product_id and previous is not None:
                from medinv.modules.catalog.application.change_bundle import product_draft_values

                previous = product_draft_values(repo, body.product_id, previous)
            values = {**(previous or {}), **body.values}
            node = category_node(
                current["payload"], str(body.category_node_id), creating=not body.product_id
            )
            check_trade_fields(ctx, current["payload"], node["field_profile_id"], values, body)
            issues = validation(
                repo,
                ctx,
                current["payload"],
                node["field_profile_id"],
                values,
                body.values,
                previous,
                True,
                body.product_id,
            )
            return {"valid": not issues, "issues": issues}

    def save(self, ctx: TenantContext, body: MasterSave, key: str) -> dict[str, Any]:
        ctx.require("catalog.write")
        return command(
            self.uow,
            ctx,
            "master.product.save",
            str(body.product_id or "new"),
            key,
            body.model_dump(mode="json"),
            lambda repo: self.insert(repo, ctx, body),
        )

    def insert(
        self, repo: SqlSession, ctx: TenantContext, body: MasterSave, *, stage_changes: bool = True
    ) -> dict[str, Any]:
        schema_lock(repo, ctx)
        current = ensure_schema(repo, ctx)
        schema = current["payload"]
        if body.form_revision != current["revision"]:
            raise BusinessError(
                "FORM_VERSION_CONFLICT",
                "配置已发布新版本，输入已保留；请重新加载配置并校验后保存",
                409,
            )
        row = raw_record(repo, body.product_id, lock=True) if body.product_id else None
        if row and row["version"] != body.expected_version:
            raise BusinessError(
                "VERSION_CONFLICT", "商品被其他人修改，输入已保留，请比较最新资料", 409
            )
        if row and actual_node(row) != str(body.category_node_id):
            raise BusinessError(
                "CATEGORY_CHANGE_RESTRICTED",
                "已有商品禁止在普通编辑中改品类；须经专项分类迁移核对历史引用",
                422,
            )
        node = category_node(schema, str(body.category_node_id), creating=row is None)
        profile = node["field_profile_id"]
        previous = values_of(repo, row) if row else None
        from medinv.modules.catalog.application.change_bundle import (
            pending,
            product_draft_values,
            stage,
        )

        draft = pending(repo, "product", row["id"]) if row and stage_changes else None
        if row and stage_changes and previous is not None:
            previous = product_draft_values(repo, row["id"], previous)
        values = {**(previous or {}), **body.values}
        if stage_changes:
            check_trade_fields(ctx, schema, profile, values, body)
        if not values.get("record_status"):
            values["record_status"] = "ACTIVE"
        issues = validation(
            repo,
            ctx,
            schema,
            profile,
            values,
            body.values if stage_changes else {},
            previous,
            False,
            body.product_id if stage_changes else None,
        )
        if issues:
            raise BusinessError(
                "MASTER_VALIDATION", "；".join(i["message"] for i in issues[:10]), 422
            )
        if row and stage_changes:
            changed = {k for k in body.values if values.get(k) != (previous or {}).get(k)}
            direct = {f["key"] for f in FIELDS if f["tab"] in {"operations", "price"}}
            if draft or (
                row["approval_state"] == "APPROVED"
                and (
                    not changed <= direct
                    or business_rules_changed(repo, row["form_revision"], profile, current)
                )
            ):
                write_trade_fields(repo, ctx, row["id"], body)
                staged_body = body.model_dump(mode="json")
                active_values = values_of(repo, row)
                staged_body["values"] = {
                    k: v
                    for k, v in values.items()
                    if k not in EXTERNAL and v != active_values.get(k)
                }
                staged_body["basic_unit_barcode"] = None
                staged_body["reference_prices"] = None
                staged_result = stage(
                    repo,
                    ctx,
                    "product",
                    row,
                    "master",
                    staged_body,
                    body.reason,
                    {"values": values_of(repo, row)},
                )
                return {
                    **staged_result,
                    "version": int(staged_result["version"]),
                    "data_version": row["data_version"],
                    "form_revision": current["revision"],
                    "code": row["code"],
                    "approval_state": row["approval_state"],
                }
        pid = body.product_id or uuid4()
        if row:
            if values.get("code") != row["code"]:
                raise BusinessError(
                    "IDENTITY_IMMUTABLE", "已有商品编码保持稳定，不能通过普通编辑更换", 422
                )
            skus = repo.rows("SELECT base_unit FROM med.sku WHERE product_id=:p", {"p": pid})
            if previous and values.get("base_unit") != previous.get("base_unit") and skus:
                if len(skus) != 1 or skus[0]["base_unit"]:
                    raise BusinessError(
                        "UNIT_IMMUTABLE", "已有库存单位不得直接改变；请使用包装换算维护", 422
                    )
                repo.execute(
                    "UPDATE med.sku SET base_unit=:unit WHERE product_id=:p",
                    {"p": pid, "unit": values.get("base_unit") or ""},
                )
            code = row["code"]
        else:
            code, _ = resolve_product_codes(
                repo, ctx, PROFILES[profile], values.get("code") or None, None
            )
        values["code"] = code
        params = {
            "t": ctx.tenant_id,
            "p": pid,
            "actor": ctx.principal_id,
            "category": PROFILES[profile],
            "node": body.category_node_id,
            "form": current["revision"],
            **{k: values.get(k) or "" for k in PRODUCT_FIELDS},
        }
        params["device_class"] = values.get("device_class") or "UNKNOWN"
        params["drug_sale_class"] = values.get("drug_sale_class") or "UNKNOWN"
        if row:
            changed = {k for k in body.values if values.get(k) != (previous or {}).get(k)}
            direct = {f["key"] for f in FIELDS if f["tab"] in {"operations", "price"}}
            params["state"] = (
                row["approval_state"]
                if changed <= direct
                and not business_rules_changed(repo, row["form_revision"], profile, current)
                else "DRAFT"
            )
            repo.execute(
                "UPDATE med.product SET "
                + ",".join(f"{k}=:{k}" for k in PRODUCT_FIELDS)
                + (
                    ",category_node_id=:node,form_revision=:form,data_version=data_ver"
                    "sion+1,version=version+1,approval_state=:state,revision_editors=a"
                    "rray_append(revision_editors,:actor),updated_by=:actor,updated_at"
                    "=now() WHERE id=:p"
                ),
                params,
            )
        else:
            result = repo.rows(
                (
                    "INSERT INTO med.product(tenant_id,id,created_by,updated_by,catego"
                    "ry,approval_state,manufacturer_name,category_node_id,form_revisio"
                    "n,"
                )
                + ",".join(PRODUCT_FIELDS)
                + ") VALUES(:t,:p,:actor,:actor,:category,'DRAFT','',:node,:form,"
                + ",".join(":" + k for k in PRODUCT_FIELDS)
                + ") ON CONFLICT(tenant_id,code) DO NOTHING RETURNING id",
                params,
            )
            if not result:
                raise BusinessError("CODE_EXISTS", "商品编码已存在", 409)
            if profile != "service":
                result = repo.rows(
                    (
                        "INSERT INTO med.sku(tenant_id,id,created_by,updated_by,product_id"
                        ",code,base_unit,qty_scale)\n"
                        "                  VALUES(:t,:id,:actor,:actor,:p,:code,:unit,0) "
                        "ON CONFLICT(tenant_id,code) DO NOTHING RETURNING id"
                    ),
                    {**params, "id": uuid4(), "unit": values.get("base_unit") or ""},
                )
                if not result:
                    raise BusinessError("CODE_EXISTS", "商品 / SKU 编码已存在", 409)
        resolve_references(
            repo, ctx, pid, values, CERT_TYPES.get(regulatory_role(profile, values) or "")
        )
        # Revalidate resolved identities, including duplicate roles and certificates.
        # Any failure rolls back both the product and newly added dictionary records.
        resolved_issues = validation(
            repo,
            ctx,
            schema,
            profile,
            values,
            body.values if stage_changes else {},
            previous,
            False,
        )
        if resolved_issues:
            raise BusinessError(
                "MASTER_VALIDATION", "；".join(i["message"] for i in resolved_issues[:10]), 422
            )
        detail_keys = [f["key"] for f in DETAIL_FIELDS] + ["base_unit"]
        detail = {k: values.get(k) if values.get(k) != "" else None for k in detail_keys}
        for definition in DETAIL_FIELDS:
            key = definition["key"]
            if detail.get(key) is not None and definition["value_type"] == "integer":
                detail[key] = int(Decimal(str(detail[key])))
            if detail.get(key) is not None and definition["value_type"] == "decimal":
                detail[key] = Decimal(str(detail[key]))
        custom = {
            f["key"]: values.get(f["key"])
            for f in schema["fields"]
            if f["binding_key"].startswith("custom.")
        }
        for definition in schema["fields"]:
            key = definition["key"]
            if custom.get(key) not in (None, "") and definition["value_type"] == "integer":
                custom[key] = int(Decimal(str(custom[key])))
        repo.execute(
            "INSERT INTO med.product_master_detail(tenant_id,product_id,"
            + ",".join(detail_keys)
            + ",custom_values) VALUES(:t,:p,"
            + ",".join(":" + k for k in detail_keys)
            + ",CAST(:custom AS jsonb)) ON CONFLICT(tenant_id,product_id) DO UPDATE SET "
            + ",".join(f"{k}=EXCLUDED.{k}" for k in detail_keys)
            + ",custom_values=EXCLUDED.custom_values",
            {**detail, "t": ctx.tenant_id, "p": pid, "custom": json.dumps(custom)},
        )
        for field in RELATIONS - {"product_certificates"}:
            if field not in body.values:
                continue
            role_filter = {
                "manufacturer": "role_code IN ('MANUFACTURER','CONTRACT_MANUFACTURER')",
                "service_provider": "role_code='SERVICE_PROVIDER'",
                "regulatory_subject": (
                    "role_code NOT IN ('MANUFACTURER','CONTRACT_MANUFACTURER','SERVICE_PROVIDER')"
                ),
            }[field]
            repo.execute(
                "DELETE FROM med.product_organization_role WHERE product_id=:p AND " + role_filter,
                {"p": pid},
            )
            for relation in values.get(field) or []:
                repo.execute(
                    (
                        "INSERT INTO med.product_organization_role(tenant_id,id,product_id"
                        ",organization_id,role_code,evidence_ref,source_certificate_id,sou"
                        "rce_certificate_version)\n"
                        "                  VALUES(:t,:id,:p,:organization_id,:role_code,:e"
                        "vidence,:certificate,:cv)"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "id": uuid4(),
                        "p": pid,
                        **relation,
                        "evidence": relation.get("evidence_ref", ""),
                        "certificate": relation.get("source_certificate_id"),
                        "cv": relation.get("source_certificate_version"),
                    },
                )
        if "product_certificates" in body.values:
            repo.execute("DELETE FROM med.product_certificate_link WHERE product_id=:p", {"p": pid})
            for link in values.get("product_certificates") or []:
                certificate = repo.rows(
                    "SELECT * FROM med.product_certificate WHERE id=:id FOR SHARE",
                    {"id": link["certificate_id"]},
                )[0]
                if int(certificate["version"]) != int(link["certificate_version"]):
                    raise BusinessError(
                        "CERTIFICATE_VERSION_CONFLICT", "证件已被修订，请重新选择", 409
                    )
                repo.execute(
                    (
                        "INSERT INTO med.product_certificate_link(tenant_id,product_id,cer"
                        "tificate_id,certificate_version,snapshot,business_use)\n"
                        " VALUES(:t,:p,:cid,:cv,CAST(:snapshot AS jsonb),:business)"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "p": pid,
                        "cid": certificate["id"],
                        "cv": certificate["version"],
                        "snapshot": json.dumps(jsonable_encoder(certificate)),
                        "business": link.get("business_use", False),
                    },
                )
        write_trade_fields(repo, ctx, pid, body)
        saved = raw_record(repo, pid)
        snapshot = values_of(repo, saved)
        repo.execute(
            (
                "INSERT INTO med.product_master_revision(tenant_id,id,product_id,d"
                "ata_version,form_revision,category_path,snapshot,created_by)\n"
                "          VALUES(:t,:id,:p,:v,:f,CAST(:path AS "
                "jsonb),CAST(:snapshot AS jsonb),:actor)"
            ),
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "p": pid,
                "v": saved["data_version"],
                "f": current["revision"],
                "path": json.dumps(node_path(schema["nodes"], str(body.category_node_id))),
                "snapshot": json.dumps(snapshot),
                "actor": ctx.principal_id,
            },
        )
        audit(
            repo,
            ctx,
            "product",
            pid,
            "master.product.saved",
            {
                "reason": body.reason,
                "data_version": saved["data_version"],
                "form_revision": current["revision"],
                "changed_fields": sorted(body.values),
            },
        )
        return {
            "id": str(pid),
            "version": saved["version"],
            "data_version": saved["data_version"],
            "form_revision": current["revision"],
            "code": code,
        }

    def organizations(self, ctx: TenantContext, search: str = "") -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            return {
                "items": jsonable_encoder(
                    SqlSession(session).rows(
                        (
                            "SELECT id,name,kind,parent_id,legal_code FROM "
                            "med.catalog_organization WHERE state='ACTIVE' AND "
                            "strpos(lower(concat_ws(' ',name,legal_code)),lower(:q))>0 ORDER "
                            "BY name,id LIMIT 200"
                        ),
                        {"q": search},
                    )
                )
            }

    def create_organization(
        self, ctx: TenantContext, body: OrganizationCreate, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            schema_lock(repo, ctx)
            if body.kind == "DEPARTMENT" and (
                not body.parent_id
                or not repo.rows(
                    (
                        "SELECT id FROM med.catalog_organization WHERE id=:id AND "
                        "kind='ORGANIZATION' AND state='ACTIVE'"
                    ),
                    {"id": body.parent_id},
                )
            ):
                raise BusinessError("ORGANIZATION_REQUIRED", "科室须属于明确的有效机构", 422)
            if body.kind == "ORGANIZATION" and body.parent_id:
                raise BusinessError("ORGANIZATION_INVALID", "企业主体不能以科室形式挂接", 422)
            target = uuid4()
            rows = repo.rows(
                (
                    "INSERT INTO med.catalog_organization(tenant_id,id,name,legal_code"
                    ",kind,parent_id,created_by)\n"
                    "              "
                    "VALUES(:t,:id,:name,:legal_code,:kind,:parent_id,:actor) ON "
                    "CONFLICT DO NOTHING RETURNING id"
                ),
                {**body.model_dump(), "t": ctx.tenant_id, "id": target, "actor": ctx.principal_id},
            )
            if not rows:
                raise BusinessError(
                    "ORGANIZATION_EXISTS",
                    "该主体标识已存在，请选择现有组织；不会按名称自动合并",
                    409,
                )
            audit(
                repo,
                ctx,
                "organization",
                target,
                "master.organization.created",
                body.model_dump(mode="json"),
            )
            return {"id": str(target)}

        return command(
            self.uow,
            ctx,
            "master.organization.create",
            "new",
            key,
            body.model_dump(mode="json"),
            run,
        )

    def certificates(
        self, ctx: TenantContext, search: str = "", product_id: UUID | None = None
    ) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                (
                    "SELECT "
                    "id,product_id,certificate_type,number,holder_name,version,state "
                    "FROM med.product_certificate WHERE state<>'INACTIVE' AND "
                    "strpos(lower(concat_ws(' ',number,holder_name)),lower(:q))>0 "
                    "ORDER BY number,id LIMIT 200"
                ),
                {"q": search},
            )
            if product_id:
                from medinv.modules.catalog.application.change_bundle import certificate_rows

                raw_record(repo, product_id)
                rows = certificate_rows(repo, "product", product_id, rows)
            return {
                "items": jsonable_encoder(
                    [
                        r
                        for r in rows
                        if r["state"] != "INACTIVE"
                        and search.lower() in (r["number"] + " " + r["holder_name"]).lower()
                    ]
                )
            }

    def listing(
        self,
        ctx: TenantContext,
        node_id: str,
        search: str,
        offset: int,
        limit: int = 50,
        sort: str = "code",
        descending: bool = False,
        state: str = "",
    ) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            schema = current_schema(repo)["payload"]
            nodes = schema["nodes"]
            node_ids = {n["node_id"] for n in nodes}
            identity = node_id or stable_id("node:catalog_root")
            if identity not in node_ids:
                raise BusinessError("CATEGORY_UNKNOWN", "品类范围不存在", 422)
            branch = descendants(nodes, identity)
            sort_column = {
                "code": "p.code",
                "name": "p.name",
                "updated_at": "p.updated_at",
                "service_price": "d.service_price",
            }.get(sort)
            if not sort_column:
                raise BusinessError("SORT_INVALID", "不支持的排序字段", 422)
            params = {
                "nodes": list(branch),
                "drug": stable_id("node:legacy_drug"),
                "device": stable_id("node:device"),
                "q": search,
                "state": state,
                "offset": offset,
                "limit": limit,
            }
            where = (
                "COALESCE(p.category_node_id,CASE WHEN p.category='DRUG' THEN "
                "CAST(:drug AS uuid) ELSE CAST(:device AS uuid) "
                "END)=ANY(CAST(:nodes AS uuid[]))\n"
                "              AND (:state='' OR p.approval_state=:state OR (:state='DRAFT' "
                "AND EXISTS(SELECT 1 FROM med.catalog_change_bundle b "
                "WHERE b.kind='product' AND b.target_id=p.id)))\n"
                "              AND (strpos(lower(concat_ws(' ',p.code,p.name,p.sea"
                "rch_alias,p.generic_name,p.manufacturer_name,p.specification,p.ap"
                "proval_number,d.brand)),lower(:q))>0\n"
                "                OR EXISTS(SELECT 1 FROM med.catalog_code c JOIN "
                "med.sku s ON (s.tenant_id,s.id)=(c.tenant_id,c.sku_id) WHERE "
                "s.product_id=p.id AND c.state='ACTIVE' AND "
                "strpos(lower(c.external_code),lower(:q))>0) "
                "OR EXISTS(SELECT 1 FROM med.product_organization_role r "
                "JOIN med.catalog_organization o ON "
                "(o.tenant_id,o.id)=(r.tenant_id,r.organization_id) "
                "WHERE r.product_id=p.id AND strpos(lower(concat_ws(' ',o.name,o.legal_code,"
                "r.role_code)),lower(:q))>0) "
                "OR EXISTS(SELECT 1 FROM med.product_certificate_link l WHERE l.product_id=p.id "
                "AND strpos(lower(l.snapshot->>'number'),lower(:q))>0) "
                "OR EXISTS(SELECT 1 FROM med.sku s WHERE s.product_id=p.id "
                "AND strpos(lower(s.base_unit),lower(:q))>0))"
            )
            total = repo.rows(
                (
                    "SELECT count(*) AS total FROM med.product p LEFT JOIN "
                    "med.product_master_detail d ON "
                    "(d.tenant_id,d.product_id)=(p.tenant_id,p.id) WHERE "
                )
                + where,
                params,
            )[0]["total"]
            rows = repo.rows(
                (
                    "SELECT p.*,EXISTS(SELECT 1 FROM med.catalog_change_bundle b "
                    "WHERE b.kind='product' AND b.target_id=p.id) AS has_changes "
                    "FROM med.product p LEFT JOIN med.product_master_detail"
                    " d ON (d.tenant_id,d.product_id)=(p.tenant_id,p.id) WHERE "
                )
                + where
                + f" ORDER BY {sort_column} {'DESC' if descending else 'ASC'}"
                + " NULLS LAST,p.id LIMIT :limit OFFSET :offset",
                params,
            )
            items = []
            related = related_records(repo, [row["id"] for row in rows])
            for row in rows:
                values = values_of(repo, row, related)
                node = category_node(schema, actual_node(row))
                profile = node["field_profile_id"]
                states = {
                    f["key"]: "NO_PERMISSION"
                    if f["read_permission"] not in ctx.permissions
                    else "UNCONFIRMED"
                    if f.get("unconfirmed")
                    else "NOT_APPLICABLE"
                    if not f["applicable"]
                    else "UNCONFIRMED"
                    if f["key"] in {"regulatory_subject", "manufacturer"}
                    and not values.get(f["key"])
                    and (
                        row["manufacturer_name"]
                        or row["authorization_holder"]
                        or row["registrant_name"]
                    )
                    else "MISSING"
                    if values.get(f["key"]) in (None, "", [])
                    else "READ_ONLY"
                    if f["read_only"]
                    else "VALUE"
                    for f in presentation(schema, profile, values)
                }
                items.append(
                    {
                        "id": str(row["id"]),
                        "version": row["version"],
                        "category_node_id": actual_node(row),
                        "category_path": node_path(nodes, actual_node(row)),
                        "approval_state": row["approval_state"],
                        "has_changes": row["has_changes"],
                        "values": safe_values(ctx, schema, values),
                        "states": states,
                    }
                )
            filter_where = where[where.index("AND (:state") + 4 :]
            counts = repo.rows(
                "SELECT p.category_node_id,p.category,count(*) AS count FROM med.product p "
                "LEFT JOIN med.product_master_detail d ON "
                "(d.tenant_id,d.product_id)=(p.tenant_id,p.id) WHERE "
                + filter_where
                + " GROUP BY p.category_node_id,p.category",
                params,
            )
            countmap = {
                n["node_id"]: sum(
                    r["count"]
                    for r in counts
                    if (
                        str(r["category_node_id"])
                        if r["category_node_id"]
                        else stable_id(
                            "node:legacy_drug" if r["category"] == "DRUG" else "node:device"
                        )
                    )
                    in descendants(nodes, n["node_id"])
                )
                for n in nodes
            }
            return {
                "items": items,
                "total": total,
                "has_more": offset + len(items) < total,
                "counts": countmap,
                "columns": [
                    {**c, "label": "上市许可持有人 / 注册人 / 备案人"}
                    if c["key"] == "regulatory_subject"
                    else c
                    for c in schema["columns"]
                ],
                "scope": node_path(nodes, identity),
            }

    def import_csv(
        self, ctx: TenantContext, body: MasterImport, key: str | None = None
    ) -> dict[str, Any]:
        ctx.require("catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            current = current_schema(repo)
            schema = current["payload"]
            if current["revision"] != body.form_revision:
                raise BusinessError("FORM_VERSION_CONFLICT", "导入配置已过期，请重新预览", 409)
            node = category_node(schema, str(body.category_node_id), creating=True)
            reader = csv.DictReader(io.StringIO(body.content.lstrip("\ufeff")))
            allowed = {f["key"]: f for f in schema["fields"] if f["value_type"] != "collection"}
            if (
                not reader.fieldnames
                or len(set(reader.fieldnames)) != len(reader.fieldnames)
                or set(reader.fieldnames) - set(allowed)
            ):
                raise BusinessError(
                    "IMPORT_HEADER_INVALID", "导入使用稳定字段键；表头未知、重复或含关系集合", 422
                )
            rows = list(reader)
            if not 1 <= len(rows) <= 100:
                raise BusinessError("IMPORT_LIMIT", "每批导入 1 至 100 行", 422)
            errors, commands = [], []
            used_codes: set[str] = set()
            for index, row in enumerate(rows):
                values: dict[str, Any] = {}
                for k, v in row.items():
                    if k is None or v is None:
                        errors.append({"row": index + 2, "message": "列数与表头不符"})
                        continue
                    values[k] = (
                        None
                        if v == ""
                        else (
                            v.lower() == "true"
                            if allowed[k]["value_type"] == "boolean"
                            and v.lower() in {"true", "false"}
                            else v
                        )
                    )
                issues = validation(
                    repo, ctx, schema, node["field_profile_id"], values, values, None, False
                )
                if values.get("code"):
                    code = str(values["code"])
                    if code in used_codes or repo.rows(
                        "SELECT id FROM med.product WHERE code=:code UNION ALL "
                        "SELECT id FROM med.sku WHERE code=:code LIMIT 1",
                        {"code": code},
                    ):
                        errors.append({"row": index + 2, "message": "商品编码重复或已经存在"})
                    used_codes.add(code)
                errors.extend({"row": index + 2, **issue} for issue in issues)
                commands.append(
                    MasterSave(
                        category_node_id=body.category_node_id,
                        form_revision=body.form_revision,
                        values=values,
                        reason=body.reason,
                    )
                )
            if errors:
                return {"valid": False, "errors": errors, "count": len(rows)}
            if key is None:
                return {"valid": True, "errors": [], "count": len(rows)}
            return {
                "valid": True,
                "items": [self.insert(repo, ctx, item) for item in commands],
                "count": len(rows),
            }

        if key is not None:
            return command(
                self.uow,
                ctx,
                "master.import",
                str(body.category_node_id),
                key,
                body.model_dump(mode="json"),
                run,
            )
        with self.uow.open(ctx, readonly=True) as session:
            return run(SqlSession(session))
