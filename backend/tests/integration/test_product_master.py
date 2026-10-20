from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from medinv.modules.catalog.api.master import (
    MasterImport,
    MasterSave,
    OrganizationCreate,
    SchemaPublish,
    SchemaSave,
)
from medinv.modules.catalog.application.master import ProductMaster
from medinv.modules.catalog.application.master_config import (
    MasterConfiguration,
    configuration_errors,
)
from medinv.modules.catalog.domain.master_schema import (
    PROFILES,
    SUBTYPES,
    default_schema,
    field,
    presentation,
    stable_id,
)
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def master(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    return ProductMaster(world.uow), MasterConfiguration(world.uow), ctx, world


def body(profile="daily", **values):
    return MasterSave(
        category_node_id=UUID(stable_id("node:" + profile)),
        form_revision=0,
        values={
            "name": "测试商品",
            "base_unit": "件",
            "regulatory_subtype": SUBTYPES[profile][0][0],
            **values,
        },
    )


@pytest.mark.parametrize("profile", list(PROFILES))
def test_all_categories_real_storage_no_physical_service_sku(master, profile):
    service, config, ctx, world = master
    result = service.save(ctx, body(profile), str(uuid4()))
    detail = service.detail(ctx, UUID(result["id"]))
    assert detail["profile"] == profile
    assert detail["values"]["name"] == "测试商品"
    assert len(detail["skus"]) == (0 if profile == "service" else 1)
    again = service.save(
        ctx,
        MasterSave(
            product_id=UUID(result["id"]),
            category_node_id=UUID(detail["category_node_id"]),
            form_revision=0,
            expected_version=1,
            values={"brand": "品牌"} if profile != "service" else {"service_price": "12.50"},
        ),
        str(uuid4()),
    )
    assert again["version"] == 2 and again["data_version"] == 2
    assert service.detail(ctx, UUID(result["id"]))["values"]["name"] == "测试商品"
    assert service.history(ctx, UUID(result["id"]), 1)["snapshot"]["name"] == "测试商品"


def test_configuration_cycles_immutable_semantics_and_fixed_slots():
    schema = default_schema()
    assert configuration_errors(schema, schema) == []
    for profile in PROFILES:
        assert len([f for f in presentation(schema, profile, {}) if f["tab"] == "basic"]) == 15
    changed = deepcopy(schema)
    changed["nodes"][1]["parent_id"] = changed["nodes"][2]["node_id"]
    assert any("循环" in e for e in configuration_errors(changed, schema))
    changed = deepcopy(schema)
    changed["slots"][0]["field_key"] = "device_class"
    assert configuration_errors(changed, schema)
    changed = deepcopy(schema)
    changed["rules"]["drug"]["dosage_form"]["label"] = "器械类别"
    assert configuration_errors(changed, schema)


def test_roles_types_omissions_and_tenant_isolation(master):
    service, config, ctx, world = master
    org = service.create_organization(ctx, OrganizationCreate(name="真实主体"), str(uuid4()))
    role = {"organization_id": org["id"], "role_code": "DEVICE_FILER", "evidence_ref": "原件"}
    saved = service.save(
        ctx, body("device", device_class="I", regulatory_subject=[role]), str(uuid4())
    )
    req = MasterSave(
        product_id=UUID(saved["id"]),
        category_node_id=UUID(stable_id("node:device")),
        form_revision=0,
        expected_version=1,
        values={"device_class": "II"},
    )
    with pytest.raises(BusinessError, match="角色"):
        service.save(ctx, req, str(uuid4()))
    with pytest.raises(BusinessError, match="不适用"):
        service.save(ctx, body("food", dosage_form="注射剂"), str(uuid4()))
    with pytest.raises(BusinessError, match="数值"):
        service.save(ctx, body("service", service_price="NaN"), str(uuid4()))
    other = replace(ctx, tenant_id=uuid4())
    with pytest.raises(BusinessError, match="不可见"):
        service.detail(other, UUID(saved["id"]))
    with pytest.raises(BusinessError, match="组织"):
        service.save(
            other, body("device", device_class="I", regulatory_subject=[role]), str(uuid4())
        )


def test_publish_stale_forms_history_and_parent_scopes(master):
    service, config, ctx, world = master
    for p in ["food", "health", "fsmp", "infant"]:
        service.save(ctx, body(p), str(uuid4()))
    branch = service.listing(ctx, stable_id("node:food_group"), "", 0)
    assert branch["total"] == 4 and len({i["id"] for i in branch["items"]}) == 4
    assert service.listing(ctx, stable_id("node:special_food"), "", 0)["total"] == 3
    payload = config.get(ctx)["payload"]
    payload["rules"]["food"]["brand"]["required"] = True
    draft = config.save(
        ctx, SchemaSave(base_revision=0, payload=payload, reason="测试发布"), str(uuid4())
    )
    assert draft["preview"]["products_requiring_revalidation"] == 1
    config.publish(
        ctx,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1, preview_token=draft["preview"]["preview_token"], reason="发布"
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError, match="新版本"):
        service.save(ctx, body("daily"), str(uuid4()))
    updated = body("food")
    updated.form_revision = 1
    result = service.save(ctx, updated, str(uuid4()))
    assert any(i["field"] == "brand" for i in service.detail(ctx, UUID(result["id"]))["issues"])
    assert config.snapshot(ctx, UUID(draft["id"]))["state"] == "PUBLISHED"


def test_import_matches_manual_and_does_not_invent_values(master):
    service, config, ctx, world = master
    request = MasterImport(
        category_node_id=UUID(stable_id("node:daily")),
        form_revision=0,
        content="name,base_unit,regulatory_subtype\n日用甲,件,GENERAL_GOODS\n日用乙,个,GENERAL_GOODS",
    )
    assert service.import_csv(ctx, request)["count"] == 2
    imported = service.import_csv(ctx, request, str(uuid4()))
    assert imported["count"] == 2
    invalid = MasterImport(
        category_node_id=UUID(stable_id("node:daily")),
        form_revision=0,
        content="name,device_class\n非法日用品,III",
    )
    assert service.import_csv(ctx, invalid)["valid"] is False


def test_private_field_read_and_omission_preserves_value(master):
    service, config, ctx, world = master
    payload = config.get(ctx)["payload"]
    f = field("custom_private", "质量内部备注", "custom", binding="custom")
    f["lifecycle"] = "CUSTOM"
    f["read_permission"] = "catalog.review"
    payload["fields"].append(f)
    payload["columns"].append(
        {
            "key": f["key"],
            "label": f["default_label"],
            "value_type": f["value_type"],
            "projection": f["key"],
        }
    )
    payload["slots"].append(
        {
            "slot_id": "C_private",
            "field_key": f["key"],
            "tab": "custom",
            "position": 0,
            "height": 94,
            "span": 1,
        }
    )
    for rules in payload["rules"].values():
        rules[f["key"]] = {
            "applicable": True,
            "label": "",
            "required": False,
            "read_only": False,
            "required_when": None,
        }
    draft = config.save(
        ctx, SchemaSave(base_revision=0, payload=payload, reason="权限字段"), str(uuid4())
    )
    config.publish(
        ctx,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1, preview_token=draft["preview"]["preview_token"], reason="权限验证"
        ),
        str(uuid4()),
    )
    request = body("daily", custom_private="内部内容")
    request.form_revision = 1
    result = service.save(ctx, request, str(uuid4()))
    restricted = replace(ctx, permissions=frozenset({"catalog.read", "catalog.write"}))
    detail = service.detail(restricted, UUID(result["id"]))
    assert "custom_private" not in detail["values"]
    service.save(
        restricted,
        MasterSave(
            product_id=UUID(result["id"]),
            category_node_id=UUID(detail["category_node_id"]),
            form_revision=1,
            expected_version=1,
            values={"brand": "更新品牌"},
        ),
        str(uuid4()),
    )
    assert service.detail(ctx, UUID(result["id"]))["values"]["custom_private"] == "内部内容"
    assert "custom_private" not in service.history(restricted, UUID(result["id"]), 1)["snapshot"]


def test_shared_certificate_explicit_business_selection_and_real_purchase(master):
    from medinv.modules.catalog.api.certificates import CertificateCreate, CertificateReview
    from medinv.modules.catalog.api.schemas import Review
    from medinv.modules.catalog.application.certificates import ProductCertificates
    from medinv.modules.catalog.application.profile import product_snapshot
    from medinv.modules.catalog.application.service import CatalogService
    from medinv.modules.procurement.application.service import ProcurementService
    from medinv.platform.database.sql import SqlSession

    service, config, ctx, world = master
    reviewer = replace(ctx, principal_id=uuid4())
    org = service.create_organization(ctx, OrganizationCreate(name="共用备案主体"), str(uuid4()))
    producer = service.create_organization(
        ctx, OrganizationCreate(name="独立生产企业"), str(uuid4())
    )
    relation = {"organization_id": org["id"], "role_code": "DEVICE_FILER", "evidence_ref": "原件"}
    first = service.save(
        ctx,
        body(
            "device",
            device_class="I",
            regulatory_subject=[relation],
            manufacturer=[
                {
                    "organization_id": producer["id"],
                    "role_code": "MANUFACTURER",
                }
            ],
        ),
        str(uuid4()),
    )
    certificates = ProductCertificates(world.uow)
    cert = certificates.save(
        ctx,
        UUID(first["id"]),
        CertificateCreate(
            certificate_type="DEVICE_FILING",
            number="MDM-FILE",
            holder_name="共用备案主体",
            issuer="测试备案机关",
            validity_mode="NOT_STATED",
            evidence_ref="原件",
            reason="建档",
        ),
        str(uuid4()),
    )
    verified = certificates.review(
        reviewer,
        UUID(first["id"]),
        UUID(cert["id"]),
        CertificateReview(
            expected_version=cert["version"],
            decision="VERIFY",
            evidence_ref="核验原件",
            reason="核验",
        ),
        str(uuid4()),
    )
    link = {
        "certificate_id": cert["id"],
        "certificate_version": int(verified["version"]),
        "business_use": True,
    }
    second = service.save(
        ctx,
        body(
            "device", device_class="I", regulatory_subject=[relation], product_certificates=[link]
        ),
        str(uuid4()),
    )
    request = MasterSave(
        product_id=UUID(first["id"]),
        category_node_id=UUID(stable_id("node:device")),
        form_revision=0,
        expected_version=1,
        values={"product_certificates": [link]},
    )
    first = service.save(ctx, request, str(uuid4()))
    catalog = CatalogService(world.uow)
    for product in [first, second]:
        catalog.review(
            reviewer,
            "product",
            UUID(product["id"]),
            Review(
                expected_version=str(product["version"]),
                decision="APPROVE",
                evidence_ref="资料原件",
                reason="审核",
            ),
            str(uuid4()),
        )
    detail = service.detail(ctx, UUID(second["id"]))
    assert len(detail["values"]["product_certificates"]) == 1
    assert service.listing(ctx, stable_id("node:device"), "测试商品", 0)["total"] == 2
    assert service.listing(ctx, "", "共用备案主体", 0)["total"] == 2
    result = service.listing(ctx, "", "MDM-FILE", 0, limit=1)
    next_page = service.listing(ctx, "", "MDM-FILE", 1, limit=1)
    assert result["total"] == 2 and next_page["total"] == 2
    assert result["columns"] == next_page["columns"]
    assert result["items"][0]["id"] != next_page["items"][0]["id"]
    with world.uow.open(ctx) as session:
        repo = SqlSession(session)
        products = ProcurementService(world.uow).products(
            repo, ctx, world.entity, [UUID(detail["skus"][0]["id"])], {"categories": ["DEVICE"]}
        )
        assert next(iter(products.values()))["qualification_current"] is True
        snapshot = product_snapshot(next(iter(products.values())))
        assert snapshot["approval_number"] == "MDM-FILE"
        assert snapshot["manufacturer_name"] == ""
        assert snapshot["master_identity"]["organizations"][0]["role_code"] == "DEVICE_FILER"
        assert snapshot["master_identity"]["business_certificate"]["version"] == 2
    posted_snapshot = post_master_purchase(world, ctx, reviewer, UUID(first["id"]))
    # Tightened business rules cannot be bypassed by a previously approved product
    # or by editing only an operational field after publication.
    payload = config.get(ctx)["payload"]
    payload["rules"]["device"]["device_model"]["required"] = True
    draft = config.save(
        ctx, SchemaSave(base_revision=0, payload=payload, reason="补齐型号"), str(uuid4())
    )
    config.publish(
        ctx,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1,
            preview_token=draft["preview"]["preview_token"],
            reason="发布型号规则",
        ),
        str(uuid4()),
    )
    with world.uow.open(ctx) as session:
        with pytest.raises(BusinessError, match="规则已变更"):
            ProcurementService(world.uow).products(
                SqlSession(session),
                ctx,
                world.entity,
                [UUID(detail["skus"][0]["id"])],
                {"categories": ["DEVICE"]},
            )
    service.save(
        ctx,
        MasterSave(
            product_id=UUID(second["id"]),
            category_node_id=UUID(stable_id("node:device")),
            form_revision=1,
            expected_version=detail["version"],
            values={"brand": "经营名称变更"},
        ),
        str(uuid4()),
    )
    assert service.detail(ctx, UUID(second["id"]))["approval_state"] == "DRAFT"
    certificates.review(
        reviewer,
        UUID(first["id"]),
        UUID(cert["id"]),
        CertificateReview(
            expected_version=verified["version"],
            decision="INACTIVATE",
            evidence_ref="失效证据",
            reason="已停用",
        ),
        str(uuid4()),
    )
    with world.uow.open(ctx) as session:
        with pytest.raises(BusinessError, match="商品未审核"):
            ProcurementService(world.uow).products(
                SqlSession(session),
                ctx,
                world.entity,
                [UUID(detail["skus"][0]["id"])],
                {"categories": ["DEVICE"]},
            )
    assert snapshot["master_identity"]["business_certificate"]["state"] == "VERIFIED"
    assert world.scalar("SELECT product_snapshot FROM med.posting_line") == posted_snapshot


def post_master_purchase(world, ctx, reviewer, product):
    from datetime import datetime, timedelta

    from medinv.modules.catalog.api.schemas import PartyCreate, Review
    from medinv.modules.catalog.application.service import CatalogService
    from medinv.modules.procurement.api.schemas import (
        InspectionCreate,
        OrderCreate,
        ReceiptCreate,
        ReceiptPost,
        Transition,
    )
    from medinv.modules.procurement.application.service import ZONE, ProcurementService

    catalog = CatalogService(world.uow)
    service = ProcurementService(world.uow)
    day = datetime.now(ZONE).date()
    party = catalog.create_party(
        ctx,
        PartyCreate(
            entity_id=world.entity,
            code="MDM-SUP",
            name="主数据贯通供应商",
            credit_code="TEST",
            kind="SUPPLIER",
            license_no="TEST",
            valid_from=day - timedelta(days=1),
            valid_until=day + timedelta(days=365),
            categories=["DEVICE"],
        ),
        str(uuid4()),
    )
    catalog.review(
        reviewer,
        "party",
        UUID(party["id"]),
        Review(
            expected_version="1",
            decision="APPROVE",
            evidence_ref="测试原件",
            reason="测试审核",
        ),
        str(uuid4()),
    )
    sku = ProductMaster(world.uow).detail(ctx, product)["skus"][0]["id"]
    order = service.create_order(
        ctx,
        OrderCreate(
            entity_id=world.entity,
            supplier_id=party["id"],
            warehouse_id=world.warehouse,
            lines=[{"sku_id": sku, "qty": "2", "unit_price": "10", "tax_rate": "0.13"}],
        ),
        str(uuid4()),
    )
    oid = UUID(order["id"])
    service.transition(
        reviewer, oid, Transition(expected_version="1", reason="审核"), str(uuid4()), True
    )
    detail = service.detail(ctx, oid)
    receipt = service.receive(
        ctx,
        oid,
        ReceiptCreate(
            expected_version=detail["order"]["version"],
            shipment_ref="MDM-DELIVERY",
            transport_note="常温",
            lines=[
                {
                    "order_line_id": detail["lines"][0]["id"],
                    "location_id": world.location,
                    "qty": "2",
                    "lot_no": "MDM-LOT",
                    "mfg_date": day - timedelta(days=1),
                    "expiry_date": day + timedelta(days=365),
                }
            ],
        ),
        str(uuid4()),
    )
    arrival = service.detail(ctx, oid)["receipts"][0]
    inspected = service.inspect(
        reviewer,
        UUID(receipt["id"]),
        InspectionCreate(
            expected_version=arrival["version"],
            note="质量验收",
            lines=[
                {
                    "receipt_line_id": arrival["lines"][0]["id"],
                    "qualified_qty": "2",
                    "rejected_qty": "0",
                }
            ],
        ),
        str(uuid4()),
    )
    posting = UUID(inspected["receipt_posting_id"])
    service.post(
        reviewer,
        posting,
        ReceiptPost(
            expected_version="1",
            inspection_id=inspected["inspection_id"],
            posting_segment_id=posting,
        ),
        str(uuid4()),
    )
    snapshot = world.scalar("SELECT product_snapshot FROM med.posting_line")
    assert snapshot["manufacturer_name"] == "独立生产企业"
    assert snapshot["approval_number"] == "MDM-FILE"
    assert snapshot["master_identity"]["product_id"] == str(product)
    assert world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=sku) == 2
    assert world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 20
    return snapshot


def test_entity_settings_independent_scope_version_and_purchase_gate(master):
    from medinv.modules.catalog.api.master import EntitySettingsSave
    from medinv.modules.catalog.application.entity_settings import (
        ProductEntitySettings,
        entity_product_allowed,
    )
    from medinv.platform.database.sql import SqlSession

    service, config, ctx, world = master
    saved = service.save(ctx, body("device", device_class="I"), str(uuid4()))
    pid = UUID(saved["id"])
    before = service.detail(ctx, pid)
    settings = ProductEntitySettings(world.uow)
    assert settings.get(ctx, pid, world.entity)["inherited"] is True
    request = EntitySettingsSave(
        entity_id=world.entity,
        expected_version=0,
        purchase_enabled=False,
        business_note="本主体暂停采购",
        reason="经营管理",
    )
    settings.save(ctx, pid, request, str(uuid4()))
    after = service.detail(ctx, pid)
    assert after["version"] == before["version"]
    assert after["data_version"] == before["data_version"]
    assert after["approval_state"] == before["approval_state"]
    with pytest.raises(BusinessError):
        settings.get(ctx, pid, uuid4())
    with pytest.raises(BusinessError, match="已变化"):
        settings.save(ctx, pid, request, str(uuid4()))
    with world.uow.open(ctx) as session:
        with pytest.raises(BusinessError, match="购销方向"):
            entity_product_allowed(
                SqlSession(session), world.entity, [UUID(after["skus"][0]["id"])], "purchase"
            )
        entity_product_allowed(
            SqlSession(session), world.entity, [UUID(after["skus"][0]["id"])], "sale"
        )


def test_incomplete_draft_can_complete_unit_and_rejects_typed_overflow(master):
    service, config, ctx, world = master
    saved = service.save(ctx, body("daily", base_unit=None), str(uuid4()))
    request = MasterSave(
        product_id=UUID(saved["id"]),
        category_node_id=UUID(stable_id("node:daily")),
        form_revision=0,
        expected_version=1,
        values={"base_unit": "盒"},
    )
    service.save(ctx, request, str(uuid4()))
    assert service.detail(ctx, UUID(saved["id"]))["skus"][0]["base_unit"] == "盒"
    request.expected_version = 2
    request.values = {"temperature_min": "1234567.1"}
    with pytest.raises(BusinessError, match="精度"):
        service.save(ctx, request, str(uuid4()))


def test_version_race_and_disabled_ancestor(master):
    from concurrent.futures import ThreadPoolExecutor

    service, config, ctx, world = master
    saved = service.save(ctx, body(), str(uuid4()))

    def update(label):
        try:
            return service.save(
                ctx,
                MasterSave(
                    product_id=UUID(saved["id"]),
                    category_node_id=UUID(stable_id("node:daily")),
                    form_revision=0,
                    expected_version=1,
                    values={"brand": label},
                ),
                str(uuid4()),
            )
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["甲", "乙"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "VERSION_CONFLICT" in results
    payload = config.get(ctx)["payload"]
    next(n for n in payload["nodes"] if n["node_code"] == "food_group")["enabled"] = False
    draft = config.save(
        ctx, SchemaSave(base_revision=0, payload=payload, reason="停用食品分支"), str(uuid4())
    )
    config.publish(
        ctx,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1, preview_token=draft["preview"]["preview_token"], reason="发布"
        ),
        str(uuid4()),
    )
    request = body("health")
    request.form_revision = 1
    with pytest.raises(BusinessError, match="祖先"):
        service.save(ctx, request, str(uuid4()))
