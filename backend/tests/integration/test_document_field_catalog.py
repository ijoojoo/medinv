"""Published MDM coverage, real related data, snapshots and permissions."""

import json
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from medinv.modules.catalog.api.master import SchemaPublish, SchemaSave
from medinv.modules.catalog.application.entry import EntryProducts
from medinv.modules.catalog.application.master_config import MasterConfiguration
from medinv.modules.catalog.domain.master_schema import TABS, field
from medinv.modules.procurement.application.document_projection import master_values, project
from medinv.modules.sales.application import amendments
from medinv.modules.workspace.document_columns import DocumentColumns
from tests.integration.test_document_columns import save_body
from tests.integration.test_procurement import flow  # noqa: F401
from tests.integration.test_sales import sale  # noqa: F401
from tests.integration.test_sales_revisions import body as revision_body

pytestmark = pytest.mark.integration


def publish_private(f):
    config = MasterConfiguration(f.world.uow)
    payload = config.get(f.maker)["payload"]
    custom = field("custom_private", "内部质量说明", "custom", binding="custom")
    custom.update(lifecycle="CUSTOM", read_permission="catalog.review")
    payload["fields"].append(custom)
    payload["columns"].append(
        dict(
            key=custom["key"],
            label=custom["default_label"],
            value_type="text",
            projection=custom["key"],
        )
    )
    payload["slots"].append(
        dict(
            slot_id="C_private",
            field_key=custom["key"],
            tab="custom",
            position=0,
            height=94,
            span=1,
        )
    )
    for rules in payload["rules"].values():
        rules[custom["key"]] = dict(
            applicable=True, label="", required=False, read_only=False, required_when=None
        )
    draft = config.save(
        f.maker, SchemaSave(base_revision=0, payload=payload, reason="全字段验证"), str(uuid4())
    )
    config.publish(
        f.maker,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1,
            preview_token=draft["preview"]["preview_token"],
            reason="发布全字段验证",
        ),
        str(uuid4()),
    )
    f.world.execute(
        """INSERT INTO med.product_master_detail(tenant_id,product_id,custom_values)
           VALUES(:t,:p,CAST(:v AS jsonb))""",
        t=f.maker.tenant_id,
        p=UUID(f.product["id"]),
        v=json.dumps({"custom_private": "内部原始值"}),
    )
    return payload


def seed_related(f):
    w, ctx, pid = f.world, f.maker, UUID(f.product["id"])
    w.execute(
        """INSERT INTO med.sku_reference_price
           (tenant_id,id,sku_id,purchase_price,wholesale_price,retail_price,created_by,updated_by)
           VALUES(:t,:i,:s,0,18.5,23,:a,:a)""",
        t=ctx.tenant_id,
        i=uuid4(),
        s=f.sku,
        a=ctx.principal_id,
    )
    w.execute(
        """INSERT INTO med.product_entity_settings
           (tenant_id,product_id,entity_id,recommendation_level,abc_class,business_note,updated_by)
           VALUES(:t,:p,:e,'重点','A','主体甲备注',:a)""",
        t=ctx.tenant_id,
        p=pid,
        e=w.entity,
        a=ctx.principal_id,
    )
    for scheme, namespace, code in [
        ("BARCODE", "", "001234567890"),
        ("MEITUAN", "店铺甲", "00045"),
        ("MEITUAN", "店铺乙", "00046"),
    ]:
        w.execute(
            """INSERT INTO med.catalog_code
               (tenant_id,id,sku_id,scheme,namespace,external_code,evidence_ref,created_by,updated_by)
               VALUES(:t,:i,:s,:scheme,:ns,:code,'测试凭证',:a,:a)""",
            t=ctx.tenant_id,
            i=uuid4(),
            s=f.sku,
            scheme=scheme,
            ns=namespace,
            code=code,
            a=ctx.principal_id,
        )
    return pid


@pytest.mark.parametrize("stage", ["orders", "receiving", "inspection", "posting", "sales_orders"])
def test_all_published_fields_are_available_with_groups_and_permissions(flow, stage):  # noqa: F811
    f = flow
    schema = publish_private(f)
    service = DocumentColumns(f.world.uow)
    reply = service.detail(f.maker, f.world.entity, stage, "editor")
    fields = {v["key"]: v for v in reply["fields"]}
    for original in schema["fields"]:
        key = {"name": "product", "manufacturer": "manufacturer_name"}.get(
            original["key"], original["key"]
        )
        assert key in fields
    assert {tab for tab, _ in TABS} <= {v["group"] for v in fields.values()}
    assert {
        "purchase_price",
        "operation_abc_class",
        "basic_barcode",
        "code_meituan",
    } <= fields.keys()
    assert not fields["purchase_price"]["editable"]
    if stage in {"orders", "receiving", "sales_orders"}:
        assert fields["qty"]["editable"]
    else:
        assert fields["qualified_qty"]["editable"] == (stage == "inspection")
    saved = service.save(
        f.maker,
        f.world.entity,
        stage,
        "editor",
        save_body(reply, custom_private={"visible": True}),
        str(uuid4()),
    )
    assert len(saved["columns"]) > 60
    viewer = replace(f.maker, permissions=f.maker.permissions - {"catalog.review"})
    restricted = service.detail(viewer, f.world.entity, stage, "editor")
    assert "custom_private" not in {v["key"] for v in restricted["fields"]}
    assert "custom_private" not in {v["key"] for v in restricted["columns"]}


def test_lookup_and_purchase_snapshot_keep_real_prices_codes_entity_settings_and_private_values(
    flow,  # noqa: F811
):
    f = flow
    seed_related(f)
    publish_private(f)
    lookup = EntryProducts(f.world.uow)
    current = lookup.search(f.maker, f.world.entity, "purchase", "", sku_id=f.sku)["items"][0]
    values = current["document_values"]
    assert values["purchase_price"] == "0.000000"
    assert values["basic_barcode"] == "001234567890"
    assert "[店铺甲] 00045（个）" in values["code_meituan"]
    assert "[店铺乙] 00046（个）" in values["code_meituan"]
    assert values["operation_abc_class"] == "A"
    assert values["custom_private"] == "内部原始值"
    oid = f.order(approve=False)
    f.world.execute("UPDATE med.sku_reference_price SET purchase_price=99")
    f.world.execute("UPDATE med.product_entity_settings SET abc_class='B'")
    f.world.execute(
        "UPDATE med.product_master_detail SET custom_values=CAST(:v AS jsonb)",
        v=json.dumps({"custom_private": "新内部值"}),
    )
    doc = f.service.detail(f.maker, oid)
    stored = doc["lines"][0]["document_values"]
    assert stored["purchase_price"] == "0.000000" and stored["operation_abc_class"] == "A"
    assert stored["custom_private"] == "内部原始值"
    assert "document_snapshot" not in json.dumps(doc)
    viewer = replace(f.maker, permissions=f.maker.permissions - {"catalog.review"})
    private = f.service.detail(viewer, oid)["lines"][0]
    assert "custom_private" not in private["document_values"]
    assert private["document_status"]["custom_private"] == "无权查看"
    restricted = lookup.search(viewer, f.world.entity, "purchase", "", sku_id=f.sku)["items"][0]
    assert "custom_private" not in restricted["document_values"]
    old = f.detail()["lines"][0]
    assert old["document_status"]["custom_private"] == "历史未留存"
    document_reader = replace(f.maker, permissions=frozenset({"purchase.read"}))
    minimal = f.service.detail(document_reader, oid)["lines"][0]
    assert minimal["document_values"]["specification"] == "单件"
    assert "purchase_price" not in minimal["document_values"]


def test_entity_settings_are_scoped_and_false_is_not_missing(flow):  # noqa: F811
    f = flow
    pid = seed_related(f)
    other = uuid4()
    f.world.execute(
        """INSERT INTO med.legal_entity(tenant_id,id,created_by,updated_by,name,credit_code,status)
           VALUES(:t,:i,:a,:a,'主体乙','ENTITY-B','ACTIVE')""",
        t=f.maker.tenant_id,
        i=other,
        a=f.maker.principal_id,
    )
    f.world.execute(
        """INSERT INTO med.product_entity_settings
           (tenant_id,product_id,entity_id,abc_class,sales_enabled,updated_by)
           VALUES(:t,:p,:e,'C',false,:a)""",
        t=f.maker.tenant_id,
        p=pid,
        e=other,
        a=f.maker.principal_id,
    )
    ctx = replace(f.maker, entity_ids=f.maker.entity_ids | {other})
    lookup = EntryProducts(f.world.uow)
    a = lookup.search(ctx, f.world.entity, "purchase", "", sku_id=f.sku)["items"][0]
    b = lookup.search(ctx, other, "purchase", "", sku_id=f.sku)["items"][0]
    assert a["document_values"]["operation_abc_class"] == "A"
    assert b["document_values"]["operation_abc_class"] == "C"
    assert b["document_values"]["operation_sales_enabled"] == "否"
    assert "operation_sales_enabled" not in b["document_status"]


def test_sales_create_revise_snapshot_and_configuration_are_independent(sale):  # noqa: F811
    s = sale
    f = s.f
    seed_related(f)
    publish_private(f)
    oid = s.order(approve=False)
    original = s.detail(oid)["lines"][0]["document_values"]
    assert original["purchase_price"] == "0.000000"
    assert Decimal(original["qty"]) == 10 and original["amount"] == "200.00"
    assert Decimal(original["reserved_qty"]) == 0 and Decimal(original["fulfilled_qty"]) == 0
    f.world.execute("UPDATE med.sku_reference_price SET purchase_price=12")
    assert s.detail(oid)["lines"][0]["document_values"]["purchase_price"] == "0.000000"
    amendments.revise(s.service, f.maker, oid, revision_body(s, oid), str(uuid4()))
    new = s.detail(oid)["lines"][0]["document_values"]
    assert new["purchase_price"] == "12.000000" and new["amount"] == "180.00"
    history = amendments.history(s.service, f.maker, oid, 0)
    assert "document_snapshot" not in json.dumps(history)
    viewer = replace(f.maker, permissions=f.maker.permissions - {"catalog.review"})
    assert "内部原始值" not in json.dumps(s.service.detail(viewer, oid), ensure_ascii=False)
    assert "内部原始值" not in json.dumps(
        amendments.history(s.service, viewer, oid, 0), ensure_ascii=False
    )
    service = DocumentColumns(f.world.uow)
    f.world.execute(
        """INSERT INTO med.entry_template(tenant_id,owner_id,channel,template)
           VALUES(:t,:o,'sale',CAST(:v AS jsonb))""",
        t=f.maker.tenant_id,
        o=f.maker.principal_id,
        v=json.dumps({"columns": [{"key": "product", "label": "原销售商品列"}]}),
    )
    reply = service.detail(f.maker, f.world.entity, "sales_orders", "editor")
    assert reply["source"] == "legacy" and reply["columns"][0]["label"] == "原销售商品列"
    service.save(
        f.maker,
        f.world.entity,
        "sales_orders",
        "editor",
        save_body(reply, purchase_price={"visible": True}),
        str(uuid4()),
    )
    purchase = service.detail(f.maker, f.world.entity, "orders", "editor")
    assert not next(c for c in purchase["columns"] if c["key"] == "purchase_price")["visible"]
    # A legacy line has no snapshot; immutable stored lines must not be rewritten for this test.
    old = s.detail(oid)["lines"][0]
    project(old, old, "sales_orders")
    assert old["document_status"]["purchase_price"] == "历史未留存"
    assert old["document_values"]["amount"] == Decimal("180.00")


def test_current_permission_can_be_stricter_than_snapshot_permission():
    from medinv.modules.catalog.domain.master_schema import default_schema

    schema = default_schema()
    custom = field("custom_private", "内部质量说明", "custom", binding="custom")
    custom["read_permission"] = "catalog.review"
    schema["fields"].append(custom)
    for rules in schema["rules"].values():
        rules[custom["key"]] = {"label": ""}
    values, status = master_values(
        dict(
            values={"custom_private": "秘密"}, field_permissions={"custom_private": "catalog.read"}
        ),
        schema,
        frozenset({"catalog.read"}),
    )
    assert "custom_private" not in values and status["custom_private"] == "无权查看"
