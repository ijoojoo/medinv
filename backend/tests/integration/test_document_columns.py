import json
from dataclasses import replace
from uuid import uuid4

import pytest

from medinv.modules.procurement.application.document_projection import capture, master_values
from medinv.modules.workspace.document_columns import ColumnSave, DocumentColumns, normalize
from medinv.modules.workspace.document_fields import registry
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError
from tests.integration.test_procurement import flow  # noqa: F401

pytestmark = pytest.mark.integration


def save_body(reply, **changes):
    columns = [
        {k: v for k, v in c.items() if k not in {"required", "editable"}} for c in reply["columns"]
    ]
    for column in columns:
        column.update(changes.get(column["key"], {}))
    return ColumnSave(expected_version=reply["version"], columns=columns)


def test_profiles_modes_stages_user_tenant_entity_isolation_and_retry(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    service = DocumentColumns(world.uow)
    original = service.detail(ctx, world.entity, "orders", "editor")
    body = save_body(original, specification={"visible": True}, qty={"label": "订购数量"})
    key = str(uuid4())
    result = service.save(ctx, world.entity, "orders", "editor", body, key)
    assert result["version"] == "1"
    assert service.save(ctx, world.entity, "orders", "editor", body, key) == result
    other_entity = uuid4()
    world.execute(
        "INSERT INTO med.legal_entity(tenant_id,id,created_by,updated_by,name,credit_code,status) "
        "VALUES(:t,:e,:a,:a,'另一主体','OTHER-COLUMNS','ACTIVE')",
        t=ctx.tenant_id,
        e=other_entity,
        a=ctx.principal_id,
    )
    two_entities = replace(ctx, entity_ids=ctx.entity_ids | {other_entity})
    other = service.detail(two_entities, other_entity, "orders", "editor")
    assert other["version"] == "0"
    service.save(two_entities, other_entity, "orders", "editor", save_body(other), str(uuid4()))
    assert (
        service.detail(ctx, world.entity, "orders", "editor")["columns"][0] == result["columns"][0]
    )
    with pytest.raises(BusinessError, match="其他窗口"):
        service.save(ctx, world.entity, "orders", "editor", body, str(uuid4()))
    for stage in ("orders", "receiving", "inspection", "posting"):
        for mode in ("editor", "view"):
            reply = service.detail(ctx, world.entity, stage, mode)
            assert reply["version"] == ("1" if (stage, mode) == ("orders", "editor") else "0")
            assert len({f["key"] for f in reply["fields"]}) == len(reply["fields"])
    for other in (replace(ctx, principal_id=uuid4()), replace(ctx, tenant_id=uuid4())):
        assert service.detail(other, world.entity, "orders", "editor")["version"] == "0"
    with pytest.raises(BusinessError):
        service.detail(ctx, uuid4(), "orders", "view")
    with pytest.raises(BusinessError):
        service.save(
            replace(ctx, permissions=frozenset()),
            world.entity,
            "orders",
            "editor",
            body,
            str(uuid4()),
        )
    viewer = replace(ctx, principal_id=uuid4(), permissions=frozenset({"purchase.read"}))
    view = service.detail(viewer, world.entity, "orders", "view")
    assert (
        service.save(viewer, world.entity, "orders", "view", save_body(view), str(uuid4()))[
            "version"
        ]
        == "1"
    )


@pytest.mark.parametrize(
    "key,change",
    [
        ("qty", {"visible": False}),
        ("product", {"tab_stop": False}),
        ("qty", {"width_mode": "weight", "width": 21}),
        ("manufacturer_name", {"tab_stop": True}),
        ("price", {"width": 1}),
        ("product", {"label": " "}),
        ("specification", {"grouping": True}),
        ("amount", {"tax_display": "percent"}),
        ("tax", {"wrap": True}),
        ("qty", {"key": "untrusted_sql"}),
        ("qty", {"key": "product"}),
    ],
)
def test_server_rejects_invalid_display_config(world, key, change):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    service = DocumentColumns(world.uow)
    reply = service.detail(ctx, world.entity, "orders", "editor")
    with pytest.raises(BusinessError):
        service.save(
            ctx, world.entity, "orders", "editor", save_body(reply, **{key: change}), str(uuid4())
        )


def test_legacy_profile_and_unknown_keys_do_not_overwrite_preferences(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    service = DocumentColumns(world.uow)
    world.execute(
        "INSERT INTO med.entry_template(tenant_id,owner_id,channel,template) "
        "VALUES(:t,:o,'purchase',CAST(:v AS jsonb))",
        t=ctx.tenant_id,
        o=ctx.principal_id,
        v=json.dumps(
            {"columns": [{"key": "product", "label": "采购商品"}, {"key": "old_unknown"}]}
        ),
    )
    reply = service.detail(ctx, world.entity, "orders", "editor")
    assert reply["source"] == "legacy"
    assert reply["columns"][0]["label"] == "采购商品"
    assert any("old_unknown" in n for n in reply["notices"])
    assert reply["version"] == "0"
    assert service.detail(ctx, world.entity, "orders", "view")["source"] == "system"
    fields = registry("receiving", "editor")
    columns, _ = normalize([], fields)
    assert {c["key"] for c in columns if c["required"]} >= {
        "product",
        "qty",
        "lot_no",
        "mfg_date",
        "expiry_date",
        "location",
    }


def test_snapshots_keep_all_stages_stable_after_master_change(flow):  # noqa: F811
    f = flow
    receipt = f.receive()
    inspection = f.inspect(receipt["id"], "6", "2")
    f.post(inspection)
    before = f.detail()
    f.world.execute(
        "UPDATE med.product SET name='新名称',specification='新规格',manufacturer_name='新厂家' "
        "WHERE id=(SELECT product_id FROM med.sku WHERE id=:s)",
        s=f.sku,
    )
    after = f.detail()
    assert after["lines"][0]["document_values"] == before["lines"][0]["document_values"]
    assert after["lines"][0]["document_values"]["amount"] == "130.00"
    r = after["receipts"][0]
    assert r["lines"][0]["document_values"]["product"] == "采购测试商品"
    assert r["inspections"][0]["document_values"]["qualified_qty"] == "6.000000"
    assert r["postings"][0]["lines"][0]["document_values"]["posted_qty"] == "6.000000"
    assert len(r["lines"]) == len(r["inspections"]) == 1
    f.world.execute(
        "UPDATE med.purchase_order_line SET document_snapshot=NULL WHERE order_id=:o", o=f.oid
    )
    old = f.detail()["lines"][0]
    assert old["document_status"]["specification"] == "历史未留存"
    assert "新名称" not in old["name"]


def test_role_semantics_and_not_applicable_fields():
    snapshot = capture(
        dict(
            name="一类器械",
            code="D1",
            category="DEVICE",
            device_class="I",
            base_unit="盒",
            master_identity={
                "organizations": [
                    {"role_code": "MANUFACTURER", "name": "生产组织"},
                    {"role_code": "DEVICE_FILER", "name": "备案组织"},
                ]
            },
            manufacturer_name="生产组织",
        )
    )
    values, statuses = master_values(snapshot)
    assert values["manufacturer_name"] == "生产组织"
    assert values["regulatory_subject"] == "备案组织"
    assert "registrant_name" not in values and "filing_holder" not in values
    assert statuses["dosage_form"] == "不适用"
    assert statuses["storage_conditions"] == "未维护"


def test_multiple_batches_keep_distinct_sources_without_join_multiplication(flow):  # noqa: F811
    f = flow
    first, second = f.receive("4", "LOT-A"), f.receive("6", "LOT-B")
    f.post(f.inspect(first["id"], "2", "1"))
    f.post(f.inspect(second["id"], "4", "2"))
    detail = f.detail()
    assert len(detail["lines"]) == 1
    assert [r["lines"][0]["document_values"]["lot_no"] for r in detail["receipts"]] == [
        "LOT-A",
        "LOT-B",
    ]
    from decimal import Decimal

    assert (
        sum(Decimal(r["lines"][0]["document_values"]["posted_qty"]) for r in detail["receipts"])
        == 6
    )
