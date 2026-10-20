"""Typed document projections of published MDM fields; never expose raw relation JSON."""

from typing import Any
from uuid import UUID

from medinv.modules.catalog.application.master_config import current_schema
from medinv.modules.catalog.application.master_projection import related_records, values_of
from medinv.modules.catalog.domain.master_schema import PROFILES, TABS, default_schema, presentation
from medinv.platform.database.sql import SqlSession
from medinv.platform.tenancy.context import TenantContext

ALIASES = {"name": "product", "manufacturer": "manufacturer_name"}
PRICE_FIELDS = {
    "purchase_price": "参考采购价",
    "wholesale_price": "参考批发价",
    "retail_price": "参考零售价",
}
OP_FIELDS = {
    "enabled": "当前主体启用",
    "purchase_enabled": "允许采购",
    "sales_enabled": "允许销售",
    "recommendation_level": "推荐级别",
    "abc_class": "ABC分类",
    "abc_secondary_class": "第二ABC分类",
    "business_note": "经营备注",
}
CODE_FIELDS = {
    "UDI_DI": "UDI-DI",
    "NHSA_DRUG": "医保药品编码",
    "NHSA_CONSUMABLE": "医保耗材编码",
    "NHSA_IVD": "医保体外诊断编码",
    "MSFX": "码上放心编码",
    "MEITUAN": "美团编码",
    "ELEME": "饿了么编码",
    "TAOBAO": "淘宝编码",
    "WANGDIANTONG": "旺店通编码",
    "CUSTOM": "其他系统编码",
}


def definitions(schema: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    schema = schema or default_schema()
    result = []
    for f in schema["fields"]:
        aliases = {r[f["key"]].get("label") for r in schema["rules"].values()}
        aliases.discard("")
        aliases.discard(None)
        result.append(
            {
                "key": ALIASES.get(f["key"], f["key"]),
                "mdm_key": f["key"],
                "label": (next(iter(aliases)) if len(aliases) == 1 else f["default_label"])[:60],
                "tab": f["tab"],
                "group_label": dict(TABS)[f["tab"]],
                "type": "number"
                if f["value_type"] in {"decimal", "integer"}
                else "date"
                if f["value_type"] == "date"
                else "text",
                "categories": tuple(PROFILES[p] for p in f["profiles"]),
                "permission": f["read_permission"],
                "description": f["semantic_definition"],
            }
        )
    parents = {f["mdm_key"]: f for f in result}
    for parent, additions in [
        ("reference_prices", [(k, v, "number") for k, v in PRICE_FIELDS.items()]),
        ("operational_settings", [("operation_" + k, v, "text") for k, v in OP_FIELDS.items()]),
        ("external_codes", [("code_" + k.lower(), v, "text") for k, v in CODE_FIELDS.items()]),
        ("barcodes", [("basic_barcode", "基本单位条码", "text")]),
    ]:
        for key, label, kind in additions:
            result.append({**parents[parent], "key": key, "label": label, "type": kind})
    return result


def codes_text(items: list[dict[str, Any]]) -> str:
    return "；".join(
        f"{CODE_FIELDS.get(c['scheme'], '条码' if c['scheme'] == 'BARCODE' else c['scheme'])} "
        f"{'[' + c['namespace'] + '] ' if c['namespace'] else ''}"
        f"{c['external_code']}（{c['unit_name']}）"
        for c in items
    )


def attach(repo: SqlSession, ctx: TenantContext, entity: UUID, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ids = list({r["product_id"] for r in rows})
    schema = current_schema(repo)["payload"]
    fields = definitions(schema)
    products = {
        r["id"]: r for r in repo.rows("SELECT * FROM med.product WHERE id=ANY(:ids)", {"ids": ids})
    }
    related = related_records(repo, ids)
    operations = {
        r["product_id"]: r
        for r in repo.rows(
            "SELECT * FROM med.product_entity_settings WHERE entity_id=:e AND product_id=ANY(:ids)",
            {"e": entity, "ids": ids},
        )
    }
    department_ids = [
        d["service_department"]
        for records in related.values()
        for d in records.get("details", [])
        if d.get("service_department")
    ]
    departments = {
        r["id"]: r["name"]
        for r in repo.rows(
            "SELECT id,name FROM med.catalog_organization WHERE kind='DEPARTMENT' AND id=ANY(:ids)",
            {"ids": department_ids},
        )
    }
    for row in rows:
        product = products[row["product_id"]]
        values = values_of(repo, product, related)
        profile = product["category"].lower()
        presented = {f["key"]: f for f in presentation(schema, profile, values)}
        skuid = str(row["id"])
        codes = [c for c in values["barcodes"] + values["external_codes"] if c["sku_id"] == skuid]
        prices: dict[str, Any] = next(
            (p for p in values["reference_prices"] if p["sku_id"] == skuid), {}
        )
        ops = operations.get(
            product["id"],
            {
                **product,
                "enabled": True,
                "purchase_enabled": True,
                "sales_enabled": True,
            },
        )
        projected: dict[str, Any] = {}
        statuses: dict[str, str] = {}
        permissions: dict[str, str] = {}
        for f in fields:
            key, source = f["key"], f["mdm_key"]
            permissions[key] = f["permission"]
            if f["permission"] not in ctx.permissions:
                continue
            rule = presented[source]
            if not rule["applicable"]:
                projected[key], statuses[key] = None, "不适用"
                continue
            value = values.get(source)
            if key in PRICE_FIELDS:
                value = prices.get(key)
            elif key.startswith("operation_"):
                value = ops.get(key.removeprefix("operation_"))
            elif key.startswith("code_"):
                value = codes_text([c for c in codes if c["scheme"].lower() == key[5:]])
            elif key == "basic_barcode":
                value = "；".join(
                    c["external_code"]
                    for c in codes
                    if c["scheme"] == "BARCODE" and not c["packaging_id"]
                )
            elif source == "reference_prices":
                value = "；".join(
                    f"{label} {prices[k]}（{row['base_unit']}）"
                    for k, label in PRICE_FIELDS.items()
                    if prices.get(k) is not None
                )
            elif source == "operational_settings":
                value = "；".join(
                    f"{label}：{('是' if ops[k] else '否') if isinstance(ops[k], bool) else ops[k]}"
                    for k, label in OP_FIELDS.items()
                    if ops.get(k) not in (None, "")
                )
            elif source in {"external_codes", "barcodes"}:
                value = codes_text(
                    [c for c in codes if (c["scheme"] == "BARCODE") == (source == "barcodes")]
                )
            elif source in {"manufacturer", "regulatory_subject", "service_provider"}:
                value = "；".join(dict.fromkeys(v["organization_name"] for v in value or []))
            elif source == "product_certificates":
                value = "；".join(c["snapshot"]["number"] for c in value or [])
            elif source == "service_department":
                value = departments.get(UUID(value), "") if value else ""
            elif rule["value_type"] == "select":
                value = next((o["label"] for o in rule["options"] if o["value"] == value), value)
            if isinstance(value, bool):
                value = "是" if value else "否"
            projected[key] = value
            if value in (None, "", []):
                statuses[key] = "未维护"
        # Core identity values use the existing confirmed/legacy adapter.
        for key in ("product", "code", "base_unit", "manufacturer_name", "regulatory_subject"):
            projected.pop(key, None)
            statuses.pop(key, None)
            permissions.pop(key, None)
        row["document_master"] = {
            "values": projected,
            "statuses": statuses,
            "permissions": permissions,
        }
