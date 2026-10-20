from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from test_catalog_revisions import body, document, review, revise
from test_procurement import flow as flow
from test_retail import retail as retail
from test_sales import sale as sale

from medinv.modules.catalog.api.schemas import PROFILE_FIELDS, ProductCreate, ProductRevision
from medinv.modules.catalog.application.profile import command_payload
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration

DEVICE = {
    "device_class": "II",
    "device_model": "MODEL-A",
    "registrant_name": "虚拟注册人",
    "registrant_address": "虚拟注册地",
    "search_alias": "  QX-SAMPLE  ",
    "trade_name": "虚拟品牌",
    "storage_conditions": "按标签密闭保存",
    "manufacturer_address": "虚拟生产地址",
}


@pytest.mark.parametrize("category", ["DRUG", "DEVICE"])
def test_create_read_search_and_retry_product_profile(flow, category):
    f = flow
    fields = (
        DEVICE
        if category == "DEVICE"
        else {
            "generic_name": "测试通用名",
            "dosage_form": "片剂",
            "authorization_holder": "虚拟持有人",
            "authorization_holder_address": "虚拟持有人地址",
            "search_alias": "YP-SAMPLE",
        }
    )
    request = ProductCreate(
        code="PROFILE",
        name="资料测试商品",
        category=category,
        manufacturer_name="虚拟厂商",
        specification="测试规格",
        approval_number="DEMO-PROFILE",
        valid_from=f.day,
        valid_until=f.day,
        sku_code="PROFILE-SKU",
        base_unit="盒",
        **fields,
    )
    key = str(uuid4())
    result = f.catalog.create_product(f.maker, request, key)
    assert f.catalog.create_product(f.maker, request, key) == result
    from medinv.modules.catalog.application.maintenance import CatalogMaintenance

    row = CatalogMaintenance(f.world.uow).detail(f.maker, "product", UUID(result["id"]), 0)[
        "record"
    ]
    for field, value in fields.items():
        assert row[field] == value.strip()
    for term in [
        fields["search_alias"].strip().lower(),
        "虚拟厂商",
        "DEMO-PROFILE",
        fields.get("generic_name", "MODEL-A"),
    ]:
        assert [r["id"] for r in f.catalog.products(f.maker, term, 0)["items"]] == [result["id"]]
    assert f.catalog.products(f.maker, "%' OR 1=1 --", 0)["items"] == []
    assert f.catalog.products(replace(f.maker, tenant_id=uuid4()), "SAMPLE", 0)["items"] == []


def test_revision_keeps_omitted_fields_and_audits_explicit_clear(flow):
    f = flow
    revise(f, "product", request=body(f, "product", **DEVICE))
    row = document(f, "product")["record"]
    assert row["device_class"] == "II" and row["search_alias"] == "QX-SAMPLE"
    with pytest.raises(BusinessError) as exc:
        review(f, "product", ctx=f.maker)
    assert exc.value.code == "SEPARATION_REQUIRED"
    review(f, "product")
    # An older client submits only its known fields; new metadata must survive.
    old_data = {
        k: v
        for k, v in body(f, "product", name="旧客户端改名").model_dump().items()
        if k not in PROFILE_FIELDS
    }
    request = ProductRevision(**old_data)
    assert not set(PROFILE_FIELDS) & command_payload(request).keys()
    revise(f, "product", request=request)
    row = document(f, "product")["record"]
    assert (
        row["device_model"] == "MODEL-A"
        and row["storage_conditions"] == DEVICE["storage_conditions"]
    )
    revise(f, "product", request=body(f, "product", search_alias=""))
    detail = document(f, "product")
    assert detail["record"]["search_alias"] == ""
    event = next(
        e["detail"] for e in detail["history"] if e["action"] == "catalog.business_updated"
    )
    assert event["before"]["search_alias"] == "QX-SAMPLE" and event["after"]["search_alias"] == ""


def test_invalid_profile_cannot_cross_category_or_bypass_database(flow):
    f = flow
    before = document(f, "product")["record"]
    with pytest.raises(BusinessError) as exc:
        revise(f, "product", request=body(f, "product", dosage_form="片剂"))
    assert exc.value.code == "PRODUCT_PROFILE_INVALID"
    assert document(f, "product")["record"] == before
    for change in ["device_class='IV'", "authorization_holder='错误类别'"]:
        with pytest.raises(DBAPIError):
            f.world.execute(
                f"UPDATE med.product SET {change} WHERE id=:id", id=UUID(f.product["id"])
            )


def test_profile_validation_and_legacy_create_payload(flow):
    f = flow
    old = {
        "code": "LEGACY",
        "name": "兼容商品",
        "category": "DEVICE",
        "drug_sale_class": "UNKNOWN",
        "manufacturer_name": "厂商",
        "specification": "规格",
        "approval_number": "DEMO",
        "valid_from": str(f.day),
        "valid_until": str(f.day),
        "sku_code": "LEGACY-SKU",
        "base_unit": "个",
        "qty_scale": 0,
    }
    assert command_payload(ProductCreate(**old)) == old
    for extra in [
        {"device_class": "IV"},
        {"dosage_form": "片剂"},
        {"device_model": 123},
        {"storage_conditions": "x" * 501},
        {"generic_name": None},
    ]:
        with pytest.raises(ValidationError):
            ProductCreate(**old, **extra)
    with pytest.raises(ValidationError):
        ProductCreate(**{**old, "category": "DRUG", "device_class": "II"})


def test_new_receipt_snapshot_contains_profile_and_stays_immutable(flow):
    f = flow
    revise(f, "product", request=body(f, "product", **DEVICE))
    review(f, "product")
    f.post(f.inspect(f.receive()["id"]))
    snapshot = f.world.scalar(
        "SELECT product_snapshot FROM med.posting_line WHERE sku_id=:s", s=f.sku
    )
    assert snapshot["device_model"] == "MODEL-A"
    assert snapshot["registrant_name"] == DEVICE["registrant_name"]
    revise(f, "product", request=body(f, "product", device_model="MODEL-B"))
    assert (
        f.world.scalar("SELECT product_snapshot FROM med.posting_line WHERE sku_id=:s", s=f.sku)
        == snapshot
    )


def test_new_sales_posting_carries_profile(sale):
    s, f = sale, sale.f
    revise(f, "product", request=body(f, "product", **DEVICE))
    review(f, "product")
    sid = s.ready("1")
    posted = s.post(sid)
    snapshot = f.world.scalar(
        "SELECT product_snapshot FROM med.posting_line WHERE posting_id=:id",
        id=UUID(posted["posting_id"]),
    )
    assert snapshot["device_class"] == "II" and snapshot["device_model"] == "MODEL-A"


def test_retail_snapshot_remembers_profile_at_creation(retail):
    r, f = retail, retail.f
    revise(f, "product", request=body(f, "product", **DEVICE))
    review(f, "product")
    created = r.create("1")
    snapshot = r.detail(created["id"])["lines"][0]["product_snapshot"]
    assert snapshot["registrant_name"] == DEVICE["registrant_name"]
    revise(f, "product", request=body(f, "product", registrant_name="更正注册人"))
    assert r.detail(created["id"])["lines"][0]["product_snapshot"] == snapshot
