from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.catalog.api.schemas import ProductCreate, Review
from medinv.modules.inventory.api.counts import CountCreate
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailCreate
from medinv.modules.retail.application.service import RetailService
from medinv.modules.sales.application.stock import SaleStockQueries
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def lookup(f, channel="wholesale", days=0, **kwargs):
    return SaleStockQueries(f.world.uow).lookup(
        kwargs.get("ctx", f.maker),
        channel,
        kwargs.get("entity", f.world.entity),
        kwargs.get("warehouse", f.world.warehouse),
        kwargs.get("skus", [f.sku]),
        days,
    )


def test_stock_query_does_not_reserve_and_actual_commands_recheck_shared_stock(sale):
    s, f = sale, sale.f
    before = f.world.scalar("SELECT count(*) FROM med.audit_event")
    assert lookup(f)["items"][0]["available_qty"] == "10.000000"
    assert f.world.scalar("SELECT count(*) FROM med.audit_event") == before
    assert f.world.scalar("SELECT count(*) FROM med.reservation") == 0
    retail = RetailService(f.world.uow)
    retail.create(
        f.maker,
        RetailCreate(
            client_operation_id=uuid4(),
            entity_id=f.world.entity,
            warehouse_id=f.world.warehouse,
            lines=[{"sku_id": f.sku, "qty": "3", "unit_price": "20", "tax_rate": "0.13"}],
        ),
        str(uuid4()),
    )
    assert lookup(f)["items"][0]["available_qty"] == "7.000000"
    with pytest.raises(BusinessError) as error:
        s.allocate("10")  # A former read of 10 cannot bypass the current stock lock.
    assert error.value.code == "INSUFFICIENT_STOCK"
    sid = s.ready("7")
    assert lookup(f, "retail")["items"][0]["available_qty"] == "0.000000"
    s.post(sid)
    assert s.scalar("on_hand") == 3 and s.scalar("reserved") == 3
    assert s.detail()["lines"][0]["remaining_qty"] == "3.000000"
    assert s.service.receivables(f.maker, f.world.entity, 0)["items"][0]["gross_amount"] == "140.00"


def test_multiple_lots_apply_shelf_life_and_exclude_expired_quarantine_and_blocked(flow):
    f = flow
    f.post(f.inspect(f.receive()["id"]))
    f.world.add_balance("2", days=10, sku=f.sku)
    f.world.add_balance("100", days=-1, sku=f.sku)
    f.world.add_balance("100", quality="QUARANTINE", sku=f.sku)
    lot, _ = f.world.add_balance("100", sku=f.sku)
    f.world.execute("UPDATE med.lot SET quality_blocked=true WHERE id=:id", id=lot)
    assert lookup(f)["items"][0]["available_qty"] == "12.000000"
    assert lookup(f, days=30)["items"][0]["available_qty"] == "10.000000"
    assert lookup(f, days=400)["items"][0]["available_qty"] == "0.000000"
    assert lookup(f, "retail")["items"][0]["available_qty"] == "12.000000"
    # The same physical SKU is returned once for repeated order lines.
    assert len(lookup(f, skus=[f.sku, f.sku])["items"]) == 1


@pytest.mark.parametrize("change", ["gate", "review", "expired", "serial", "no_lot"])
def test_product_admission_is_checked_even_when_stock_is_positive(flow, change):
    f = flow
    f.post(f.inspect(f.receive()["id"]))
    statements = {
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
        "review": "UPDATE med.product SET approval_state='DRAFT' WHERE id=:p",
        "expired": "UPDATE med.product SET valid_until=:day WHERE id=:p",
        "serial": "UPDATE med.sku SET serial_required=true WHERE id=:s",
        "no_lot": "UPDATE med.sku SET lot_required=false WHERE id=:s",
    }
    f.world.execute(
        statements[change], s=f.sku, p=UUID(f.product["id"]), day=f.day - timedelta(days=1)
    )
    for channel in ("wholesale", "retail"):
        row = lookup(f, channel)["items"][0]
        assert row["available_qty"] == "0.000000" and row["sale_eligible"] is False


@pytest.mark.parametrize("classification", ["UNKNOWN", "RX", "OTC"])
def test_retail_lookup_respects_drug_classification(flow, classification):
    f = flow
    product = f.catalog.create_product(
        f.maker,
        ProductCreate(
            code="D-STOCK",
            name="可售库存药品",
            category="DRUG",
            drug_sale_class=classification,
            manufacturer_name="测试厂家",
            specification="一盒",
            approval_number="TEST",
            valid_from=f.day,
            valid_until=f.day + timedelta(days=60),
            sku_code="D-STOCK",
            base_unit="盒",
            qty_scale=0,
        ),
        str(uuid4()),
    )
    f.catalog.review(
        f.reviewer,
        "product",
        UUID(product["id"]),
        Review(expected_version="1", decision="APPROVE", evidence_ref="TEST", reason="独立核定"),
        str(uuid4()),
    )
    sku = UUID(product["sku_id"])
    f.world.add_balance("8", sku=sku)
    f.world.execute(
        """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id)
        VALUES(:t,:id,:a,:a,:e,:s)""",
        t=f.maker.tenant_id,
        id=uuid4(),
        a=f.maker.principal_id,
        e=f.world.entity,
        s=sku,
    )
    assert lookup(f, skus=[sku])["items"][0]["available_qty"] == "8.000000"
    item = lookup(f, "retail", skus=[sku])["items"][0]
    assert item["available_qty"] == ("8.000000" if classification == "OTC" else "0.000000")
    assert item["sale_eligible"] is (classification == "OTC")


def test_count_freeze_removes_and_cancel_restores_sale_availability(flow):
    f = flow
    f.post(f.inspect(f.receive()["id"]))
    balance = f.world.scalar("SELECT id FROM med.stock_balance WHERE sku_id=:s", s=f.sku)
    counts = CountService(f.world.uow)
    cid = UUID(
        counts.create(
            f.maker,
            CountCreate(
                client_operation_id=uuid4(),
                entity_id=f.world.entity,
                warehouse_id=f.world.warehouse,
                balance_ids=[balance],
                reason="实盘",
            ),
            str(uuid4()),
        )["id"]
    )
    for channel in ("wholesale", "retail"):
        assert lookup(f, channel)["items"][0]["available_qty"] == "0.000000"
    counts.cancel(f.maker, cid, Transition(expected_version="1", reason="取消实盘"), str(uuid4()))
    assert lookup(f)["items"][0]["available_qty"] == "10.000000"


def test_queries_reject_cross_scope_and_do_not_require_inventory_permission(flow):
    f = flow
    for channel, permission in [("wholesale", "sales.read"), ("retail", "retail.read")]:
        ctx = replace(f.maker, permissions=frozenset([permission]))
        # Qualified product with no balance yet is explicitly zero, not a missing row.
        assert lookup(f, channel, ctx=ctx)["items"][0]["available_qty"] == "0.000000"
        for changes in [
            {"ctx": replace(ctx, permissions=frozenset(["inventory.read"]))},
            {"ctx": replace(ctx, entity_ids=frozenset())},
            {"ctx": replace(ctx, tenant_id=uuid4())},
            {"entity": uuid4()},
            {"warehouse": uuid4()},
            {"skus": [uuid4()]},
        ]:
            with pytest.raises(BusinessError):
                lookup(f, channel, **changes)


def test_http_stock_query_validates_bounds_and_channel_permissions(api, flow):
    client, owner = api
    f = flow
    params = {
        "entity_id": str(f.world.entity),
        "warehouse_id": str(f.world.warehouse),
        "sku_id": str(f.sku),
    }
    assert client.get("/api/v1/selling/stock", params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['sales.read']
                     WHERE tenant_id=:t AND principal_id=:a"""),
            {"t": f.maker.tenant_id, "a": f.maker.principal_id},
        )
    response = client.get("/api/v1/selling/stock", params=params)
    assert response.status_code == 200, response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["sku_id"] == str(f.sku)
    assert client.get("/api/v1/retail/stock", params=params).status_code == 403
    for changes in [
        {"sku_id": []},
        {"sku_id": [str(f.sku)] * 51},
        {"sku_id": "invalid"},
        {"minimum_shelf_days": -1},
        {"minimum_shelf_days": 3651},
    ]:
        assert client.get("/api/v1/selling/stock", params={**params, **changes}).status_code == 422
    assert (
        client.get(
            "/api/v1/selling/stock", params={**params, "warehouse_id": str(uuid4())}
        ).status_code
        == 404
    )
    client.headers["X-Context-Generation"] = "2"
    assert client.get("/api/v1/selling/stock", params=params).status_code == 409
