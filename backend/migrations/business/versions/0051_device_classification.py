"""Add explicit device classification and versioned editable product checks."""

import json
from copy import deepcopy
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision = "b0051"
down_revision = "b0050"
branch_labels = None
depends_on = None
FIELD_PACK = [
    {
        "field_id": "75fdf1f8-85c2-5856-ac14-8653f242c830",
        "key": "device_classification_code",
        "default_label": "器械分类编码",
        "semantic_definition": "器械分类编码；商品主数据，不记录本次交易或批次事实",
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "product.device_classification_code",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["device"],
        "required": False,
        "options": [],
        "max_length": 30,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对证照填写，例如 06-01-01 / 2017；与Ⅰ、Ⅱ、Ⅲ管理类别分别维护。",
    },
    {
        "field_id": "8fa32a63-fa70-5d73-9758-329c025a451e",
        "key": "device_classification_version",
        "default_label": "器械分类目录版本",
        "semantic_definition": "器械分类目录版本；商品主数据，不记录本次交易或批次事实",
        "value_type": "text",
        "cardinality": "one",
        "widget_type": "text",
        "binding_key": "product.device_classification_version",
        "data_scope": "PRODUCT",
        "lifecycle": "BUILTIN",
        "tab": "quality",
        "profiles": ["device"],
        "required": False,
        "options": [],
        "max_length": 30,
        "read_permission": "catalog.read",
        "write_permission": "catalog.write",
        "alias_allowed": True,
        "help_text": "按已核对证照填写，例如 06-01-01 / 2017；与Ⅰ、Ⅱ、Ⅲ管理类别分别维护。",
    },
]
SYSTEM_ACTOR = UUID("00000000-0000-0000-0000-000000000051")


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
            slot_id = "b0051_" + key
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
            "product",
            sa.Column(field["key"], sa.String(30), nullable=False, server_default=""),
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
            VALUES(:t,:id,:actor,'system:migration:b0051','master.schema.extended',
                'catalog_schema',:target,:request,:reason)"""),
            {
                "t": old["tenant_id"],
                "id": uuid4(),
                "actor": SYSTEM_ACTOR,
                "target": target,
                "request": uuid4(),
                "reason": json.dumps(
                    {
                        "migration": "b0051",
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

    op.execute("""
    CREATE OR REPLACE FUNCTION med.product_qualification_current(p med.product) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER AS $$
      WITH config AS (SELECT COALESCE((SELECT payload FROM med.qualification_rules
        WHERE tenant_id=p.tenant_id),'{}'::jsonb) AS q)
      SELECT p.record_status='ACTIVE' AND p.category<>'SERVICE' AND CASE
        WHEN NOT COALESCE((q->>'product_certificate_required')::boolean,true) THEN true
        WHEN p.form_revision IS NULL THEN
          NOT COALESCE((q->>'product_certificate_dates')::boolean,true)
          OR COALESCE((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
          BETWEEN p.valid_from AND p.valid_until,false)
        WHEN p.category IN ('FOOD','DAILY') THEN true
        WHEN p.category='HERB' AND EXISTS(SELECT 1 FROM med.product_master_detail d
          WHERE (d.tenant_id,d.product_id)=(p.tenant_id,p.id)
          AND d.regulatory_subtype='ORDINARY_HERB') THEN true
        WHEN p.category='INFANT' AND EXISTS(SELECT 1 FROM med.product_master_detail d
          WHERE (d.tenant_id,d.product_id)=(p.tenant_id,p.id)
          AND d.regulatory_subtype='INFANT_OTHER') THEN true
        ELSE EXISTS(SELECT 1 FROM med.product_certificate_link l JOIN med.product_certificate c
          ON (c.tenant_id,c.id)=(l.tenant_id,l.certificate_id)
          WHERE (l.tenant_id,l.product_id)=(p.tenant_id,p.id) AND l.business_use
          AND c.state='VERIFIED' AND c.version=l.certificate_version
          AND (NOT COALESCE((q->>'product_certificate_dates')::boolean,true)
            OR c.validity_mode='NOT_STATED' OR (c.validity_mode='FIXED'
            AND (statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
            BETWEEN c.valid_from AND c.valid_until)))
        END FROM config;
    $$;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
