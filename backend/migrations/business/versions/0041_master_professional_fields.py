"""Append professional fields and publish a new snapshot without changing old versions."""

import json
from copy import deepcopy
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision = "b0041"
down_revision = "b0040"
branch_labels = None
depends_on = None

# Frozen at migration creation; never import the evolving application vocabulary here.
FIELD_PACK = [
    {
        "field_id": "1e538023-6154-5555-ac38-4893879007d2",
        "key": "herb_source_note",
        "default_label": "药材基原范围说明",
        "semantic_definition": (
            "药材基原范围说明；商品主数据，不记录本次交易或批次事实；保留"
            "原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.herb_source_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["herb"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "标准允许的基原；每批实际基原另行记录",
    },
    {
        "field_id": "70d0a6de-69ca-584f-8c7a-e644c7fbcd06",
        "key": "herb_adjuvants_note",
        "default_label": "炮制辅料说明",
        "semantic_definition": (
            "炮制辅料说明；商品主数据，不记录本次交易或批次事实；保留原文"
            "说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.herb_adjuvants_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["herb"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "cab9ead5-6a12-5a4a-a866-21963cf67da7",
        "key": "herb_decoction_note",
        "default_label": "特殊煎煮 / 服用方法（原文）",
        "semantic_definition": (
            "特殊煎煮 / 服用方法（原文）；商品主数据，不记录本次交易或"
            "批次事实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.herb_decoction_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["herb"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "bdbb7433-84ba-5e2d-817c-d9b48cb16f5a",
        "key": "herb_toxicity_note",
        "default_label": "中药毒性描述（原文）",
        "semantic_definition": (
            "中药毒性描述（原文）；商品主数据，不记录本次交易或批次事实；"
            "保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.herb_toxicity_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["herb"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "5350eed0-83c6-542a-958f-ef89c9a8f0f2",
        "key": "food_intake_note",
        "default_label": "食用量与频次（标签原文）",
        "semantic_definition": (
            "食用量与频次（标签原文）；商品主数据，不记录本次交易或批次事"
            "实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.food_intake_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["food", "health", "fsmp", "infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "2b2c7550-e596-5b0b-979c-54fc2f3d39e0",
        "key": "food_reconstitution_note",
        "default_label": "冲调 / 再制方法（标签原文）",
        "semantic_definition": (
            "冲调 / 再制方法（标签原文）；商品主数据，不记录本次交易或"
            "批次事实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.food_reconstitution_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["food", "health", "fsmp", "infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "0a7afc4c-a160-5ebf-b2f0-07937b765cdf",
        "key": "food_opened_storage_note",
        "default_label": "开封后贮存与食用期限",
        "semantic_definition": (
            "开封后贮存与食用期限；商品主数据，不记录本次交易或批次事实；"
            "保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.food_opened_storage_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["food", "health", "fsmp", "infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "开封后的要求；区别于未开封保质期",
    },
    {
        "field_id": "4f57545a-d992-5b5d-a8d9-73e0c097d9b8",
        "key": "food_warning_note",
        "default_label": "食用警示（标签原文）",
        "semantic_definition": (
            "食用警示（标签原文）；商品主数据，不记录本次交易或批次事实；"
            "保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.food_warning_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["food", "health", "fsmp", "infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "b4ed8cfd-da85-54ef-b103-b72cc804a9b9",
        "key": "health_precautions",
        "default_label": "保健食品注意事项",
        "semantic_definition": (
            "保健食品注意事项；商品主数据，不记录本次交易或批次事实；保留"
            "原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.health_precautions",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["health"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "115c01bc-e9ae-57cc-951a-31c8a75257f3",
        "key": "fsmp_formula_features",
        "default_label": "特医配方特点 / 营养学特征",
        "semantic_definition": (
            "特医配方特点 / 营养学特征；商品主数据，不记录本次交易或批"
            "次事实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.fsmp_formula_features",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["fsmp"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "fd70bd18-184f-5581-bce2-eb5ba0e68e50",
        "key": "fsmp_guidance_notice",
        "default_label": "医生 / 临床营养师指导提示",
        "semantic_definition": (
            "医生 / 临床营养师指导提示；商品主数据，不记录本次交易或批"
            "次事实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.fsmp_guidance_notice",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["fsmp"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "3f3895e7-7376-5bbf-bceb-669027f5ed84",
        "key": "fsmp_sole_source_note",
        "default_label": "唯一营养来源适用说明",
        "semantic_definition": (
            "唯一营养来源适用说明；商品主数据，不记录本次交易或批次事实；"
            "保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.fsmp_sole_source_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["fsmp"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "d3b81585-9ffe-5bfe-884b-575940ead4a7",
        "key": "infant_animal_source_note",
        "default_label": "配方乳动物性来源（原文）",
        "semantic_definition": (
            "配方乳动物性来源（原文）；商品主数据，不记录本次交易或批次事"
            "实；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.infant_animal_source_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "3507ae2e-5d7e-5eaa-af50-d5b1d371dc7c",
        "key": "infant_production_process_note",
        "default_label": "配方乳生产工艺（原文）",
        "semantic_definition": (
            "配方乳生产工艺（原文）；商品主数据，不记录本次交易或批次事实"
            "；保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.infant_production_process_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["infant"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对的标签、标准或项目资料填写",
    },
    {
        "field_id": "d925b4c0-21b9-5d0e-a578-d7d7659f1b1d",
        "key": "service_scope_description",
        "default_label": "服务内容与边界",
        "semantic_definition": (
            "服务内容与边界；商品主数据，不记录本次交易或批次事实；保留原"
            "文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.service_scope_description",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["service"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "项目资料说明；实际收费按适用地区政策核定",
    },
    {
        "field_id": "e4abb883-89c3-5d04-ac39-4765fc8913f0",
        "key": "service_expected_output",
        "default_label": "服务产出",
        "semantic_definition": (
            "服务产出；商品主数据，不记录本次交易或批次事实；保留原文说明"
            "，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.service_expected_output",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["service"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "项目资料说明；实际收费按适用地区政策核定",
    },
    {
        "field_id": "84e9d99a-ff6c-5fe5-a75c-52a8766c1fda",
        "key": "service_price_components",
        "default_label": "价格构成说明",
        "semantic_definition": (
            "价格构成说明；商品主数据，不记录本次交易或批次事实；保留原文"
            "说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.service_price_components",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["service"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "项目资料说明；实际收费按适用地区政策核定",
    },
    {
        "field_id": "449787b0-aa63-58b7-ae73-9af20276472a",
        "key": "service_billing_note",
        "default_label": "计价说明（核定原文）",
        "semantic_definition": (
            "计价说明（核定原文）；商品主数据，不记录本次交易或批次事实；"
            "保留原文说明，不替代结构化规则或业务执行记录"
        ),
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "detail.service_billing_note",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "extension",
        "profiles": ["service"],
        "required": False,
        "options": [],
        "max_length": 500,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "项目资料说明；实际收费按适用地区政策核定",
    },
]
SYSTEM_ACTOR = UUID("00000000-0000-0000-0000-000000000041")


def extend_schema(payload):
    result = deepcopy(payload)
    known = {f["key"]: f for f in result["fields"]}
    for field in FIELD_PACK:
        key = field["key"]
        if key in known:
            if known[key] != field:
                raise RuntimeError(f"Field identity collision: {key}")
            continue
        result["fields"].append(deepcopy(field))
        position = (
            max((s["position"] for s in result["slots"] if s["tab"] == field["tab"]), default=-1)
            + 1
        )
        if position >= 150:
            raise RuntimeError("No free slot for professional field pack")
        used_ids = {s["slot_id"] for s in result["slots"]}
        slot_id = f"{field['tab'][0].upper()}_{position + 1:03}"
        if slot_id in used_ids:
            slot_id = "b0041_" + key
        if slot_id in used_ids:
            raise RuntimeError(f"Slot identity collision: {key}")
        result["slots"].append(
            {
                "slot_id": slot_id,
                "field_key": key,
                "tab": field["tab"],
                "position": position,
                "height": 94,
                "span": 1,
            }
        )
        for profile, rules in result["rules"].items():
            rules[key] = {
                "applicable": profile in field["profiles"],
                "label": "",
                "required": False,
                "read_only": False,
                "required_when": None,
            }
        result["columns"].append(
            {
                "key": key,
                "label": field["default_label"],
                "value_type": field["value_type"],
                "projection": key,
            }
        )
    if len(result["fields"]) > 150:
        raise RuntimeError("Field pack exceeds configured catalog capacity")
    return result


def upgrade():
    for field in FIELD_PACK:
        op.add_column(
            "product_master_detail",
            sa.Column(field["key"], sa.String(500), nullable=True),
            schema="med",
        )
    connection = op.get_bind()
    # The migration runs as the table owner. Lift FORCE only inside this DDL transaction
    # to visit every tenant; normal RLS policies/grants are unchanged and restored below.
    connection.execute(sa.text("ALTER TABLE med.catalog_schema NO FORCE ROW LEVEL SECURITY"))
    connection.execute(sa.text("ALTER TABLE med.audit_event NO FORCE ROW LEVEL SECURITY"))
    # DDL already locks the details table; deployment pauses writers before migration.
    connection.execute(sa.text("LOCK TABLE med.catalog_schema IN SHARE ROW EXCLUSIVE MODE"))
    rows = (
        connection.execute(
            sa.text("""SELECT DISTINCT ON (tenant_id) * FROM med.catalog_schema
        WHERE state='PUBLISHED' ORDER BY tenant_id,revision DESC""")
        )
        .mappings()
        .all()
    )
    for old in rows:
        updated = extend_schema(old["payload"])
        if updated == old["payload"]:
            continue
        target = uuid4()
        connection.execute(
            sa.text("""INSERT INTO med.catalog_schema
            (tenant_id,id,revision,state,base_revision,payload,created_by,updated_by,published_at)
            VALUES(:t,:id,:revision,'PUBLISHED',:base,CAST(:payload AS jsonb),
                :actor,:actor,now())"""),
            {
                "t": old["tenant_id"],
                "id": target,
                "revision": old["revision"] + 1,
                "base": old["revision"],
                "payload": json.dumps(updated),
                "actor": SYSTEM_ACTOR,
            },
        )
        connection.execute(
            sa.text("""INSERT INTO med.audit_event
            (tenant_id,id,created_by,actor_subject,action,resource_type,resource_id,request_id,reason)
            VALUES(:t,:id,:actor,'system:migration:b0041','master.schema.extended',
                'catalog_schema',:target,:request,:reason)"""),
            {
                "t": old["tenant_id"],
                "id": uuid4(),
                "actor": SYSTEM_ACTOR,
                "target": target,
                "request": uuid4(),
                "reason": json.dumps(
                    {
                        "migration": "b0041",
                        "base_revision": old["revision"],
                        "revision": old["revision"] + 1,
                        "added_fields": [f["key"] for f in FIELD_PACK],
                    },
                    ensure_ascii=False,
                ),
            },
        )
    connection.execute(sa.text("ALTER TABLE med.catalog_schema FORCE ROW LEVEL SECURITY"))
    connection.execute(sa.text("ALTER TABLE med.audit_event FORCE ROW LEVEL SECURITY"))


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
