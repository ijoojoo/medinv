"""Cross-category projection, historical compatibility and saved preference migration."""

import json
from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from medinv.modules.catalog.api.master import OrganizationCreate
from medinv.modules.catalog.application.entry import EntryProducts
from medinv.modules.procurement.application.document_projection import capture, master_values
from medinv.modules.workspace.document_columns import (
    SUBJECT_ALIASES,
    ColumnSave,
    DocumentColumns,
    normalize,
)
from medinv.modules.workspace.document_fields import registry
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError
from tests.integration.test_procurement import flow  # noqa: F401
from tests.integration.test_product_master import body, master  # noqa: F401

pytestmark = pytest.mark.integration

CASES = [
    ("drug", "CHEMICAL", None, "DRUG_MAH"),
    ("herb", "APPROVAL_HERB", None, "DRUG_MAH"),
    ("device", "MEDICAL_DEVICE", "I", "DEVICE_FILER"),
    ("device", "MEDICAL_DEVICE", "II", "DEVICE_REGISTRANT"),
    ("device", "MEDICAL_DEVICE", "III", "DEVICE_REGISTRANT"),
    ("health", "HEALTH_REGISTERED", None, "HEALTH_REGISTRANT"),
    ("health", "HEALTH_FILED", None, "HEALTH_FILER"),
    ("fsmp", "FSMP_REGISTERED", None, "FSMP_REGISTRANT"),
    ("infant", "INFANT_POWDER", None, "INFANT_REGISTRANT"),
    ("cosmetic", "COSMETIC_GENERAL", None, "COSMETIC_FILER"),
    ("cosmetic", "COSMETIC_SPECIAL", None, "COSMETIC_REGISTRANT"),
]


@pytest.mark.parametrize("profile,subtype,device_class,role", CASES)
def test_real_mdm_names_are_selected_by_same_category_role(
    master,  # noqa: F811
    profile,
    subtype,
    device_class,
    role,
):
    service, _, ctx, world = master
    subject = service.create_organization(ctx, OrganizationCreate(name="责任主体"), str(uuid4()))
    manufacturer = service.create_organization(
        ctx, OrganizationCreate(name="独立生产企业"), str(uuid4())
    )
    values = dict(
        regulatory_subtype=subtype,
        regulatory_subject=[{"organization_id": subject["id"], "role_code": role}],
        manufacturer=[{"organization_id": manufacturer["id"], "role_code": "MANUFACTURER"}],
    )
    if device_class:
        values["device_class"] = device_class
    saved = service.save(ctx, body(profile, **values), str(uuid4()))
    detail = service.detail(ctx, UUID(saved["id"]))
    sku = UUID(detail["skus"][0]["id"])
    lookup = EntryProducts(world.uow).search(ctx, world.entity, "purchase", "", sku_id=sku)
    result = lookup["items"][0]
    assert result["document_values"]["regulatory_subject"] == "责任主体"
    assert result["document_values"]["manufacturer_name"] == "独立生产企业"
    assert result["document_values"]["code"] == detail["values"]["code"]
    assert not SUBJECT_ALIASES.intersection(result["document_values"])
    assert "regulatory_subject" not in result["document_status"]


@pytest.mark.parametrize(
    "category,subtype,device_class,expected",
    [
        ("HERB", "ORDINARY_HERB", None, "不适用"),
        ("INFANT", "INFANT_OTHER", None, "不适用"),
        ("FOOD", "PREPACKAGED", None, "不适用"),
        ("DAILY", "GENERAL_GOODS", None, "不适用"),
        ("SERVICE", "OUTPATIENT", None, "不适用"),
        ("DEVICE", "MEDICAL_DEVICE", "UNKNOWN", "待明确类别"),
        ("COSMETIC", None, None, "待明确类别"),
        ("HERB", None, None, "待明确类别"),
    ],
)
def test_subject_applicability_matches_mdm(category, subtype, device_class, expected):
    snapshot = capture(
        dict(
            category=category,
            regulatory_subtype=subtype,
            device_class=device_class,
            master_identity={"organizations": [{"role_code": "MANUFACTURER", "name": "不得代填"}]},
        )
    )
    values, status = master_values(snapshot)
    assert status["regulatory_subject"] == expected
    assert not values["regulatory_subject"]


def test_snapshot_retains_identity_role_and_keeps_other_semantics_separate():
    organization = str(uuid4())
    row = dict(
        category="DEVICE",
        device_class="I",
        product_code="PRODUCT-001",
        code="OLD-SKU-001",
        approval_number="原备案编号",
        specification="单件",
        device_model="MODEL-A",
        base_unit="个",
        master_identity={
            "organizations": [
                {"role_code": "DEVICE_FILER", "name": "备案主体", "organization_id": organization},
                {"role_code": "DEVICE_REGISTRANT", "name": "不得误取的角色"},
                {"role_code": "MANUFACTURER", "name": "不得代替主体"},
            ]
        },
    )
    snapshot = capture(row)
    assert snapshot["schema_version"] == 2
    assert snapshot["regulatory_role"] == "DEVICE_FILER"
    assert snapshot["regulatory_subjects"] == [
        {"role_code": "DEVICE_FILER", "name": "备案主体", "organization_id": organization}
    ]
    values, _ = master_values(snapshot)
    assert values["code"] == "PRODUCT-001"
    assert values["approval_number"] == "原备案编号"
    assert values["specification"] == "单件" and values["device_model"] == "MODEL-A"
    herb, statuses = master_values(
        capture(
            dict(category="HERB", regulatory_subtype="ORDINARY_HERB", generic_name="通用饮片名")
        )
    )
    assert herb["generic_name"] == "通用饮片名" and "generic_name" not in statuses


@pytest.mark.parametrize(
    "category,device_class,revision,old,expected,status",
    [
        ("DRUG", None, None, {"authorization_holder": "旧持有人"}, "旧持有人", None),
        (
            "DEVICE",
            "I",
            0,
            {"filing_holder": "旧备案人", "registrant_name": "错误角色"},
            "旧备案人",
            None,
        ),
        (
            "DEVICE",
            "I",
            None,
            {"filing_holder": "", "registrant_name": "旧表共用名"},
            "旧表共用名",
            None,
        ),
        ("DEVICE", "I", 0, {"filing_holder": "", "registrant_name": "错误角色"}, "", "未维护"),
        (
            "DEVICE",
            "II",
            0,
            {"filing_holder": "不得误取", "registrant_name": "旧注册人"},
            "旧注册人",
            None,
        ),
        ("DEVICE", "III", 0, {"registrant_name": ""}, "", "未维护"),
        ("DEVICE", "UNKNOWN", None, {"registrant_name": "原文名称"}, "原文名称", None),
        ("HERB", None, 0, {"authorization_holder": "已记录持有人"}, "已记录持有人", None),
        (
            "HEALTH",
            None,
            0,
            {"authorization_holder": "", "registrant_name": ""},
            None,
            "历史未留存",
        ),
        ("COSMETIC", None, 0, {}, None, "历史未留存"),
        ("DRUG", None, None, {}, None, "历史未留存"),
    ],
)
def test_old_snapshot_mapping_does_not_rewrite_or_invent_data(
    category, device_class, revision, old, expected, status
):
    snapshot = dict(
        schema_version=1,
        category=category,
        device_class=device_class,
        form_revision=revision,
        values=old,
    )
    before = deepcopy(snapshot)
    values, statuses = master_values(snapshot)
    assert values.get("regulatory_subject") == expected
    assert statuses.get("regulatory_subject") == status
    assert not SUBJECT_ALIASES.intersection(values)
    assert snapshot == before


def test_legacy_profiles_merge_without_writing_and_keep_user_visibility_order_width(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    service = DocumentColumns(world.uow)
    fields = registry("orders", "editor")
    defaults, _ = normalize([], fields)
    subject = next(c for c in defaults if c["key"] == "regulatory_subject")
    old = [c for c in defaults if c["key"] != "regulatory_subject"]
    aliases = [
        {**subject, "key": "authorization_holder", "label": "上市许可持有人", "visible": False},
        {
            **subject,
            "key": "registrant_name",
            "label": "责任企业",
            "visible": True,
            "width_mode": "weight",
            "width": 3,
            "wrap": True,
        },
        {**subject, "key": "filing_holder", "label": "备案人", "visible": True, "width": 330},
    ]
    old[2:2] = aliases
    template = json.dumps({"schema_version": 1, "columns": old})
    world.execute(
        """INSERT INTO med.document_column_profile
        (tenant_id,entity_id,owner_id,document_type,mode,template)
        VALUES(:t,:e,:o,'orders','editor',CAST(:template AS jsonb))""",
        t=ctx.tenant_id,
        e=world.entity,
        o=ctx.principal_id,
        template=template,
    )
    result = service.detail(ctx, world.entity, "orders", "editor")
    assert result["version"] == "1" and result["notices"]
    assert result["columns"][2]["key"] == "regulatory_subject"
    merged = result["columns"][2]
    assert merged["label"] == "责任企业" and merged["visible"] and merged["wrap"]
    assert (merged["width_mode"], merged["width"]) == ("weight", 3)
    assert world.scalar("SELECT template FROM med.document_column_profile") == json.loads(template)
    # An open pre-upgrade editor can still submit its old keys.
    request = ColumnSave(
        expected_version="1",
        columns=[{k: v for k, v in c.items() if k not in {"required", "editable"}} for c in old],
    )
    key = str(uuid4())
    saved = service.save(ctx, world.entity, "orders", "editor", request, key)
    assert service.save(ctx, world.entity, "orders", "editor", request, key) == saved
    stored = world.scalar("SELECT template FROM med.document_column_profile")["columns"]
    assert not SUBJECT_ALIASES.intersection(c["key"] for c in stored)
    assert len([c for c in stored if c["key"] == "regulatory_subject"]) == 1
    # Once explicitly configured, the canonical column wins over stale aliases.
    required = [c for c in defaults if c["key"] != "regulatory_subject"]
    migrated, _ = normalize(
        [{**merged, "visible": False}, *aliases, *required], fields, strict=True
    )
    assert migrated[0]["key"] == "regulatory_subject" and not migrated[0]["visible"]
    with pytest.raises(BusinessError):
        normalize([aliases[0], aliases[0]], fields, strict=True)


@pytest.mark.parametrize("stage", ["orders", "receiving", "inspection", "posting"])
@pytest.mark.parametrize("mode", ["editor", "view"])
def test_all_eight_contexts_offer_one_subject_and_migrate_default_label(stage, mode):
    fields = registry(stage, mode)
    assert sum(f["key"] == "regulatory_subject" for f in fields) == 1
    assert not SUBJECT_ALIASES.intersection(f["key"] for f in fields)
    subject = next(f["default"] for f in fields if f["key"] == "regulatory_subject")
    columns, _ = normalize(
        [{**subject, "key": "filing_holder", "label": "备案人", "visible": True}], fields
    )
    assert columns[0]["label"] == "持有人／注册人／备案人" and columns[0]["visible"]


def test_historical_subject_is_identical_across_all_purchase_stages(flow):  # noqa: F811
    f = flow
    old = dict(
        schema_version=1,
        category="DEVICE",
        device_class="I",
        form_revision=0,
        values={"product": "历史器械", "filing_holder": "历史备案人", "code": "历史编码"},
    )
    f.world.execute(
        "UPDATE med.purchase_order_line SET document_snapshot=CAST(:s AS jsonb) WHERE order_id=:o",
        s=json.dumps(old),
        o=f.oid,
    )
    receipt = f.receive()
    f.post(f.inspect(receipt["id"]))
    f.world.execute(
        "UPDATE med.product SET registrant_name='今天的企业',code='TODAY' "
        "WHERE id=(SELECT product_id FROM med.sku WHERE id=:s)",
        s=f.sku,
    )
    detail = f.detail()
    r = detail["receipts"][0]
    rows = [detail["lines"][0], r["lines"][0], r["inspections"][0], r["postings"][0]["lines"][0]]
    for row in rows:
        assert row["document_values"]["regulatory_subject"] == "历史备案人"
        assert row["document_values"]["code"] == "历史编码"
    assert (
        f.world.scalar(
            "SELECT document_snapshot FROM med.purchase_order_line WHERE order_id=:o", o=f.oid
        )
        == old
    )


def test_live_lookup_and_new_order_use_product_code_not_legacy_sku_code(flow):  # noqa: F811
    f = flow
    result = EntryProducts(f.world.uow).search(
        f.maker, f.world.entity, "purchase", "", sku_id=f.sku
    )["items"][0]
    assert result["code"] == "S-NEW"  # Stable lookup/import identity remains compatible.
    assert result["document_values"]["code"] == "P-NEW"
    assert f.detail()["lines"][0]["document_values"]["code"] == "P-NEW"
