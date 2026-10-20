"""Versioned master-data vocabulary. No executable tenant expressions or storage paths."""

from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid5

TABS = [
    ("basic", "基本信息"),
    ("price", "价格信息"),
    ("quality", "质量管控"),
    ("operations", "运营管控"),
    ("extension", "扩展属性"),
    ("integration", "第三方对接"),
    ("custom", "自定义属性"),
]
PROFILES = {
    "drug": "DRUG",
    "herb": "HERB",
    "device": "DEVICE",
    "food": "FOOD",
    "health": "HEALTH",
    "fsmp": "FSMP",
    "infant": "INFANT",
    "cosmetic": "COSMETIC",
    "daily": "DAILY",
    "service": "SERVICE",
}
NODES = [
    ("catalog_root", "全部商品与项目", None, None),
    ("drug_group", "药品", "catalog_root", None),
    ("drug", "中西成药", "drug_group", "drug"),
    ("herb", "中药饮片", "drug_group", "herb"),
    ("device", "医疗器械", "catalog_root", "device"),
    ("food_group", "食品", "catalog_root", None),
    ("food", "普通食品", "food_group", "food"),
    ("special_food", "特殊食品", "food_group", None),
    ("health", "保健食品", "special_food", "health"),
    ("fsmp", "特殊医学用途配方食品", "special_food", "fsmp"),
    ("infant", "婴幼儿配方食品", "special_food", "infant"),
    ("cosmetic", "化妆品", "catalog_root", "cosmetic"),
    ("daily", "日用品", "catalog_root", "daily"),
    ("service", "门诊医疗服务", "catalog_root", "service"),
    ("legacy_drug", "药品（旧档待确认细类）", "drug_group", "drug"),
]
SUBTYPES = {
    "drug": [("CHEMICAL", "化学药"), ("PATENT", "中成药"), ("BIOLOGICAL", "生物制品")],
    "herb": [("ORDINARY_HERB", "常规饮片"), ("APPROVAL_HERB", "依法实施批准管理的品种")],
    "device": [("MEDICAL_DEVICE", "医疗器械")],
    "food": [("PREPACKAGED", "预包装食品"), ("BULK", "散装食品")],
    "health": [("HEALTH_REGISTERED", "注册类"), ("HEALTH_FILED", "备案类")],
    "fsmp": [("FSMP_REGISTERED", "特医食品（注册）")],
    "infant": [("INFANT_POWDER", "婴幼儿配方乳粉"), ("INFANT_OTHER", "其他婴幼儿配方食品")],
    "cosmetic": [("COSMETIC_GENERAL", "普通化妆品"), ("COSMETIC_SPECIAL", "特殊化妆品")],
    "daily": [("GENERAL_GOODS", "一般日用品")],
    "service": [
        ("OUTPATIENT", "门诊诊疗"),
        ("OUTPATIENT_TEST", "门诊检验检查"),
        ("OUTPATIENT_TREATMENT", "门诊治疗"),
    ],
}


def stable_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "medinv:master:v1:" + key))


def field(
    key: str,
    label: str,
    tab: str = "extension",
    kind: str = "text",
    profiles: str = "*",
    binding: str = "detail",
    required: bool = False,
    options: list[tuple[str, str]] | None = None,
    scope: str = "PRODUCT",
) -> dict[str, Any]:
    return {
        "field_id": stable_id("field:" + key),
        "key": key,
        "default_label": label,
        "semantic_definition": label
        + "；"
        + (
            "组织独立设置，不是公共主档值"
            if scope != "PRODUCT"
            else "商品主数据，不记录本次交易或批次事实"
        ),
        "value_type": kind,
        "cardinality": "many" if kind == "collection" else "one",
        "widget_type": kind,
        "binding_key": binding + "." + key,
        "data_scope": scope,
        "lifecycle": "BUILTIN",
        "tab": tab,
        "profiles": profiles.split(",") if profiles != "*" else list(PROFILES),
        "required": required,
        "options": [{"value": v, "label": label} for v, label in options or []],
        "max_length": 500 if kind == "text" else None,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": key
        not in {
            "regulatory_subject",
            "manufacturer",
            "service_provider",
            "device_class",
            "dosage_form",
            "device_model",
            "product_certificates",
        },
    }


FIELDS = [
    field("code", "商品编码", "basic", binding="product"),
    field("name", "规范名称", "basic", binding="product", required=True),
    field("specification", "规格", "basic", binding="product"),
    field("base_unit", "基本单位 / 计费单位", "basic", binding="unit", required=True),
    field("regulatory_subtype", "监管子类型", "basic", "select", required=True),
    field(
        "device_class",
        "器械管理类别",
        "basic",
        "select",
        "device",
        "product",
        True,
        [("I", "Ⅰ类"), ("II", "Ⅱ类"), ("III", "Ⅲ类")],
    ),
    field(
        "regulatory_subject",
        "监管主体",
        "basic",
        "collection",
        "drug,herb,device,health,fsmp,infant,cosmetic",
        "role",
    ),
    field(
        "manufacturer",
        "生产企业",
        "basic",
        "collection",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
        "role",
    ),
    field("service_provider", "服务提供机构", "basic", "collection", "service", "role", True),
    field("dosage_form", "剂型", "basic", profiles="drug", binding="product"),
    field("device_model", "器械型号", "basic", profiles="device", binding="product"),
    field("service_department", "服务科室", "basic", "reference", "service"),
    field(
        "product_certificates",
        "产品批准 / 注册 / 备案信息",
        "basic",
        "collection",
        "drug,herb,device,health,fsmp,infant,cosmetic",
        "certificate",
    ),
    field(
        "barcodes",
        "条码与包装",
        "basic",
        "collection",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
        "codes",
    ),
    field(
        "record_status",
        "资料状态",
        "basic",
        "select",
        binding="product",
        options=[("ACTIVE", "在用"), ("INACTIVE", "停用")],
    ),
    field(
        "reference_prices",
        "参考价格（按现有 SKU）",
        "price",
        "collection",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
        "prices",
    ),
    field("service_price", "服务参考单价", "price", "decimal", "service"),
    field("price_note", "价格说明", "price", profiles="service"),
    field(
        "drug_business_scope",
        "药品经营范围类别",
        "quality",
        profiles="drug,herb",
        binding="product",
    ),
    field(
        "device_business_scope", "器械经营范围类别", "quality", profiles="device", binding="product"
    ),
    field(
        "drug_sale_class",
        "处方管理",
        "quality",
        "select",
        "drug",
        "product",
        False,
        [("OTC", "非处方药"), ("RX", "处方药")],
    ),
    field("quality_note", "质量说明", "quality", binding="product"),
    field(
        "storage_conditions",
        "储运条件",
        "quality",
        profiles="drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
        binding="product",
    ),
    field(
        "temperature_min",
        "储存温度下限（℃）",
        "quality",
        "decimal",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field(
        "temperature_max",
        "储存温度上限（℃）",
        "quality",
        "decimal",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field(
        "humidity_max",
        "相对湿度上限（%）",
        "quality",
        "decimal",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field(
        "shelf_life_months",
        "标准保质期（月）",
        "quality",
        "integer",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field(
        "lot_management",
        "批次管理规则",
        "quality",
        "boolean",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field(
        "expiry_management",
        "效期管理规则",
        "quality",
        "boolean",
        "drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field("sterile", "无菌产品", "quality", "boolean", "device"),
    field("single_use", "一次性使用", "quality", "boolean", "device"),
    field("search_alias", "检索助记码", "operations", binding="product"),
    field("trade_name", "商品名称（历史兼容字段）", "operations", binding="product"),
    field("brand", "品牌", "operations"),
    field("short_name", "简称", "operations"),
    field(
        "operational_settings",
        "经营主体独立设置",
        "operations",
        "collection",
        binding="operations",
        scope="ORGANIZATION",
    ),
    field("generic_name", "通用名称", profiles="drug,herb", binding="product"),
    field(
        "manufacturer_address",
        "生产地址（原始记载）",
        profiles="drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
        binding="product",
    ),
    field("herb_origin", "饮片标准产地", profiles="herb"),
    field("herb_processing", "炮制方法", profiles="herb"),
    field(
        "execution_standard",
        "执行标准",
        profiles="drug,herb,device,food,health,fsmp,infant,cosmetic,daily",
    ),
    field("herb_grade", "饮片等级", profiles="herb"),
    field("herb_part", "药用部位", profiles="herb"),
    field(
        "ingredients", "成分 / 配料", profiles="drug,herb,food,health,fsmp,infant,cosmetic,daily"
    ),
    field("allergens", "致敏物质提示", profiles="food,health,fsmp,infant,cosmetic"),
    field("nutrition", "营养成分标示", profiles="food,health,fsmp,infant"),
    field("target_population", "适用人群", profiles="health,fsmp,infant,cosmetic,service"),
    field("unsuitable_population", "不适宜人群", profiles="health,fsmp,infant,cosmetic,service"),
    field("health_function", "批准 / 备案保健功能", profiles="health"),
    field("fsmp_use", "特医食品适用疾病 / 医学状况", profiles="fsmp"),
    field("infant_stage", "婴幼儿适用月龄段", profiles="infant"),
    field("cosmetic_purpose", "化妆品用途 / 功效宣称", profiles="cosmetic"),
    field("cosmetic_usage", "使用方法与注意事项", profiles="cosmetic"),
    field("material", "主要材质", profiles="device,daily"),
    field("daily_use", "日用品用途", profiles="daily"),
    field("service_duration_minutes", "预计服务时长（分钟）", kind="integer", profiles="service"),
    field("service_instructions", "门诊服务说明与预约要求", profiles="service"),
    field("external_codes", "医保 / 追溯 / 平台编码", "integration", "collection", binding="codes"),
]
# Append-only vocabulary pack. Existing field identities and slots remain unchanged.
PROFESSIONAL_FIELDS = [
    field("herb_source_note", "药材基原范围说明", profiles="herb"),
    field("herb_adjuvants_note", "炮制辅料说明", profiles="herb"),
    field("herb_decoction_note", "特殊煎煮 / 服用方法（原文）", profiles="herb"),
    field("herb_toxicity_note", "中药毒性描述（原文）", "quality", profiles="herb"),
    field("food_intake_note", "食用量与频次（标签原文）", profiles="food,health,fsmp,infant"),
    field(
        "food_reconstitution_note",
        "冲调 / 再制方法（标签原文）",
        profiles="food,health,fsmp,infant",
    ),
    field(
        "food_opened_storage_note",
        "开封后贮存与食用期限",
        "quality",
        profiles="food,health,fsmp,infant",
    ),
    field(
        "food_warning_note", "食用警示（标签原文）", "quality", profiles="food,health,fsmp,infant"
    ),
    field("health_precautions", "保健食品注意事项", "quality", profiles="health"),
    field("fsmp_formula_features", "特医配方特点 / 营养学特征", profiles="fsmp"),
    field("fsmp_guidance_notice", "医生 / 临床营养师指导提示", "quality", profiles="fsmp"),
    field("fsmp_sole_source_note", "唯一营养来源适用说明", profiles="fsmp"),
    field("infant_animal_source_note", "配方乳动物性来源（原文）", profiles="infant"),
    field("infant_production_process_note", "配方乳生产工艺（原文）", profiles="infant"),
    field("service_scope_description", "服务内容与边界", profiles="service"),
    field("service_expected_output", "服务产出", profiles="service"),
    field("service_price_components", "价格构成说明", profiles="service"),
    field("service_billing_note", "计价说明（核定原文）", profiles="service"),
]
for _f in PROFESSIONAL_FIELDS:
    _f["help_text"] = "按已核对的标签、标准或项目资料填写"
    _f["semantic_definition"] += "；保留原文说明，不替代结构化规则或业务执行记录"
    if _f["key"] == "herb_source_note":
        _f["help_text"] = "标准允许的基原；每批实际基原另行记录"
    elif _f["key"] == "food_opened_storage_note":
        _f["help_text"] = "开封后的要求；区别于未开封保质期"
    elif _f["key"].startswith("service_"):
        _f["help_text"] = "项目资料说明；实际收费按适用地区政策核定"
FIELDS.extend(PROFESSIONAL_FIELDS)
FIELD_MAP = {f["key"]: f for f in FIELDS}
DETAIL_FIELDS = [f for f in FIELDS if f["binding_key"].startswith("detail.")]


def default_schema() -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    for tab, _ in TABS:
        for index, f in enumerate(f for f in FIELDS if f["tab"] == tab):
            slots.append(
                {
                    "slot_id": ("B" if tab == "basic" else tab[0].upper() + "_")
                    + f"{index + 1:03}",
                    "field_key": f["key"],
                    "tab": tab,
                    "position": index,
                    "height": 94,
                    "span": 1,
                }
            )
    return {
        "format": 1,
        "tabs": [{"id": k, "label": v} for k, v in TABS],
        "fields": deepcopy(FIELDS),
        "slots": slots,
        "nodes": [
            {
                "node_id": stable_id("node:" + code),
                "node_code": code,
                "name": name,
                "parent_id": stable_id("node:" + parent) if parent else None,
                "sort_order": i,
                "enabled": code != "legacy_drug",
                "is_assignable": bool(profile) and code != "legacy_drug",
                "field_profile_id": profile,
            }
            for i, (code, name, parent, profile) in enumerate(NODES)
        ],
        "rules": {
            p: {
                f["key"]: {
                    "applicable": p in f["profiles"],
                    "label": "",
                    "required": f["required"] and p in f["profiles"],
                    "read_only": False,
                    "required_when": None,
                }
                for f in FIELDS
            }
            for p in PROFILES
        },
        "columns": [
            {
                "key": f["key"],
                "label": f["default_label"],
                "value_type": f["value_type"],
                "projection": f["key"],
            }
            for f in FIELDS
        ],
    }


def descendants(nodes: list[dict[str, Any]], node_id: str) -> set[str]:
    found = {node_id}
    for _ in range(len(nodes)):
        new = {n["node_id"] for n in nodes if n["parent_id"] in found} - found
        if not new:
            break
        found |= new
    return found


def node_path(nodes: list[dict[str, Any]], node_id: str) -> list[str]:
    indexed = {n["node_id"]: n for n in nodes}
    path, seen = [], set()
    current: str | None = node_id
    while current and current in indexed and current not in seen:
        seen.add(current)
        n = indexed[current]
        path.append(n["name"])
        current = n["parent_id"]
    return list(reversed(path))


def regulatory_role(profile: str, values: dict[str, Any]) -> str | None:
    subtype = values.get("regulatory_subtype")
    if profile == "drug" or (profile == "herb" and subtype == "APPROVAL_HERB"):
        return "DRUG_MAH"
    if profile == "device":
        return {"I": "DEVICE_FILER", "II": "DEVICE_REGISTRANT", "III": "DEVICE_REGISTRANT"}.get(
            str(values.get("device_class"))
        )
    return {
        "HEALTH_REGISTERED": "HEALTH_REGISTRANT",
        "HEALTH_FILED": "HEALTH_FILER",
        "FSMP_REGISTERED": "FSMP_REGISTRANT",
        "INFANT_POWDER": "INFANT_REGISTRANT",
        "COSMETIC_GENERAL": "COSMETIC_FILER",
        "COSMETIC_SPECIAL": "COSMETIC_REGISTRANT",
    }.get(str(subtype))


ROLE_LABELS = {
    "DRUG_MAH": "上市许可持有人",
    "DEVICE_FILER": "医疗器械备案人",
    "DEVICE_REGISTRANT": "医疗器械注册人",
    "HEALTH_REGISTRANT": "保健食品注册人",
    "HEALTH_FILER": "保健食品备案人",
    "FSMP_REGISTRANT": "特医食品注册人",
    "INFANT_REGISTRANT": "婴幼儿配方乳粉注册人",
    "COSMETIC_FILER": "化妆品备案人",
    "COSMETIC_REGISTRANT": "化妆品注册人",
    "MANUFACTURER": "生产企业",
    "CONTRACT_MANUFACTURER": "受托生产企业",
    "SERVICE_PROVIDER": "服务提供机构",
}


def presentation(
    schema: dict[str, Any], profile: str, values: dict[str, Any]
) -> list[dict[str, Any]]:
    result = []
    for definition in schema["fields"]:
        f = deepcopy(definition)
        rule = schema["rules"][profile][f["key"]]
        f.update(rule)
        f["required"] = rule["required"] or (
            definition["required"] and profile in definition["profiles"]
        )
        f["label"] = rule["label"] or f["default_label"]
        if f["key"] == "regulatory_subtype":
            f["options"] = [{"value": v, "label": label} for v, label in SUBTYPES[profile]]
        if f["key"] == "regulatory_subject":
            f["role_code"] = regulatory_role(profile, values)
            f["label"] = ROLE_LABELS.get(f["role_code"], "监管主体（请先明确子类型 / 管理类别）")
            f["unconfirmed"] = profile in {
                "device",
                "herb",
                "health",
                "fsmp",
                "infant",
                "cosmetic",
            } and not values.get("device_class" if profile == "device" else "regulatory_subtype")
            f["applicable"] = f["role_code"] is not None or f["unconfirmed"]
            if not f["role_code"] and not f["unconfirmed"]:
                f["label"] = "上市许可持有人 / 注册人 / 备案人"
        if f["key"] == "base_unit" and profile == "service":
            f["label"] = rule["label"] or "计费单位"
        condition = rule.get("required_when")
        if condition and values.get(condition["field"]) == condition["equals"]:
            f["required"] = True
        result.append(f)
    return result
