from dataclasses import replace
from uuid import uuid4

import pytest
from test_api import api as api
from test_procurement import flow as flow
from test_purchase_sales_options import large_catalog as large_catalog

from medinv.modules.catalog.application.entry import EntryProducts
from medinv.modules.sales.application.service import SalesService
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("channel", ["purchase", "sale"])
def test_entry_search_paginates_and_matches_multiple_literal_keywords(large_catalog, channel):
    f = large_catalog
    service = EntryProducts(f.world.uow)
    first = service.search(f.maker, f.world.entity, channel, "采购 测试厂家 单件")
    assert len(first["items"]) == 20 and first["has_more"]
    ids = {p["id"] for p in first["items"]}
    for offset in range(20, 220, 20):
        page = service.search(f.maker, f.world.entity, channel, "采购 测试厂家 单件", offset)
        assert not ids.intersection(p["id"] for p in page["items"])
        ids.update(p["id"] for p in page["items"])
    assert len(ids) == 206 and not page["has_more"]
    exact = service.search(f.maker, f.world.entity, channel, "page-1")
    assert exact["items"][0]["code"] == "PAGE-1"
    for search in ["%", "_", "' OR 1=1 --", "采购 不匹配", "无此商品"]:
        assert service.search(f.maker, f.world.entity, channel, search)["items"] == []
    assert (
        service.search(replace(f.maker, tenant_id=uuid4()), f.world.entity, channel, "")["items"]
        == []
    )
    assert (
        service.search(
            replace(f.maker, tenant_id=uuid4()), f.world.entity, channel, "", sku_id=f.sku
        )["items"]
        == []
    )
    for ctx in [
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, permissions=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            service.search(ctx, f.world.entity, channel, "", sku_id=f.sku)


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE med.product SET approval_state='DRAFT'",
        "UPDATE med.product SET valid_until=current_date-1,valid_from=current_date-10",
        "UPDATE med.product SET restricted_category='SPECIAL'",
        "UPDATE med.sku SET serial_required=true",
        "UPDATE med.stock_gate SET blocked=true",
        "UPDATE med.legal_entity SET status='SUSPENDED'",
    ],
)
def test_entry_search_excludes_unavailable_but_can_label_existing_selection(flow, change):
    f = flow
    service = EntryProducts(f.world.uow)
    f.world.execute(change)
    for channel in ["purchase", "sale"]:
        assert service.search(f.maker, f.world.entity, channel, "S-NEW")["items"] == []
        selected = service.search(f.maker, f.world.entity, channel, "", sku_id=f.sku)["items"]
        assert len(selected) == 1 and selected[0]["eligible"] is False


def test_entry_search_alias_and_channel_rules(flow):
    f = flow
    f.world.execute("UPDATE med.product SET search_alias='gcsb',device_model='X-2026'")
    f.world.execute("UPDATE med.sku SET lot_required=false WHERE id=:id", id=f.sku)
    svc = EntryProducts(f.world.uow)
    rows = svc.search(f.maker, f.world.entity, "purchase", "GCSB X-2026")["items"]
    assert len(rows) == 1 and rows[0]["eligible"]
    assert rows[0]["qty_scale"] == 0 and rows[0]["base_unit"] == "个"
    assert svc.search(f.maker, f.world.entity, "sale", "GCSB X-2026")["items"] == []


@pytest.mark.parametrize("channel", ["purchase", "sale"])
def test_options_can_skip_product_catalog_without_changing_other_choices(large_catalog, channel):
    f = large_catalog
    svc = f.service if channel == "purchase" else SalesService(f.world.uow)
    for offset in [0, 200, 400]:
        old = svc.options(f.maker, f.world.entity, offset)
        new = svc.options(f.maker, f.world.entity, offset, include_skus=False)
        assert new["skus"] == []
        assert {k: v for k, v in old.items() if k != "skus"} == {
            k: v for k, v in new.items() if k != "skus"
        }


def test_entry_http_bounds_and_contract(api, world):
    client, _ = api
    params = {"entity_id": str(world.entity), "channel": "purchase"}
    path = "/api/v1/catalog/entry-products"
    # This fixture only grants inventory permissions, not purchasing access.
    assert client.get(path, params=params).status_code == 403
    for invalid in [
        {"channel": "retail"},
        {"offset": -1},
        {"offset": 100001},
        {"search": "x" * 129},
        {"sku_id": "invalid"},
    ]:
        assert client.get(path, params={**params, **invalid}).status_code == 422
    schema = client.get("/openapi.json").json()
    assert path in schema["paths"]
    for group in ["purchasing", "selling"]:
        fields = schema["paths"][f"/api/v1/{group}/options"]["get"]["parameters"]
        assert any(p["name"] == "include_skus" and p["schema"]["default"] for p in fields)
