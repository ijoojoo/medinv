from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_procurement import flow as flow

from medinv.entrypoints.core_api import create_app
from medinv.modules.inventory.api.openings import OpeningCreate, OpeningReview
from medinv.modules.inventory.api.transfers import WarehouseCreate
from medinv.modules.inventory.application import opening_posting
from medinv.modules.inventory.application.openings import OpeningService
from medinv.modules.inventory.application.warehouses import WarehouseService
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailComplete, RetailCreate, RetailPayment
from medinv.modules.retail.application import payments as retail_payments
from medinv.modules.retail.application import posting as retail_posting
from medinv.modules.retail.application.service import RetailService
from medinv.modules.sales.api.schemas import (
    CreditSet,
    Pick,
    SalesCreate,
    ShipmentCreate,
    ShipmentPost,
    Verify,
)
from medinv.modules.sales.application import fulfillment, posting
from medinv.modules.sales.application.service import SalesService
from medinv.modules.settlement.api.schemas import AccountCreate
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def opened(flow):
    f = flow
    service = OpeningService(f.world.uow)
    warehouse = WarehouseService(f.world.uow).create(
        f.maker,
        WarehouseCreate(
            client_operation_id=uuid4(),
            entity_id=f.world.entity,
            code="OPEN",
            name="开账仓库",
            location_code="A1",
        ),
        str(uuid4()),
    )
    wid, lid = UUID(warehouse["id"]), UUID(warehouse["location_id"])

    def body(**changes):
        return OpeningCreate.model_validate(
            {
                "client_operation_id": uuid4(),
                "entity_id": f.world.entity,
                "warehouse_id": wid,
                "source_ref": "OPENING-LIST",
                "reason": "开账实盘与原账面成本已核对",
                "lines": [
                    {
                        "sku_id": f.sku,
                        "location_id": lid,
                        "lot_no": "OPENING-B1",
                        "mfg_date": f.day - timedelta(days=2),
                        "expiry_date": f.day + timedelta(days=100),
                        "qty": "10",
                        "cost_amount": "100",
                    }
                ],
                **changes,
            }
        )

    def create(request=None):
        return UUID(service.create(f.maker, request or body(), str(uuid4()))["id"])

    def transition(oid):
        return Transition(
            expected_version=service.detail(f.maker, oid)["document"]["version"],
            reason="核对期初清单",
        )

    def review(oid, ctx=None, decision="APPROVE"):
        return service.transition(
            ctx or f.reviewer,
            oid,
            OpeningReview(
                **transition(oid).model_dump(), decision=decision, evidence_ref="核验清单"
            ),
            str(uuid4()),
            True,
        )

    def post(oid, ctx=None, request=None, key=None):
        return opening_posting.post(
            service, ctx or f.reviewer, oid, request or transition(oid), key or str(uuid4())
        )

    return SimpleNamespace(**locals())


def test_opening_stock_flows_through_wholesale_and_retail_with_original_cost(opened):
    x, f = opened, opened.f
    oid = x.create()
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid) == 0
    )
    x.review(oid)
    x.post(oid)
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") is None
    f.world.execute("UPDATE med.party SET kind='BOTH' WHERE id=:id", id=UUID(f.party["id"]))
    sales = SalesService(f.world.uow)
    sales.set_credit(
        f.maker,
        f.world.entity,
        UUID(f.party["id"]),
        CreditSet(
            expected_version="0",
            credit_limit="1000",
            valid_until=f.day + timedelta(days=100),
            reason="测试授信",
        ),
        str(uuid4()),
    )
    order = UUID(
        sales.create(
            f.maker,
            SalesCreate(
                entity_id=f.world.entity,
                warehouse_id=x.wid,
                customer_id=f.party["id"],
                delivery_address="测试地址",
                minimum_shelf_days=0,
                lines=[{"sku_id": f.sku, "qty": "3", "unit_price": "20", "tax_rate": "0"}],
            ),
            str(uuid4()),
        )["id"]
    )
    sales.transition(
        f.reviewer,
        order,
        Transition(expected_version="1", reason="测试销售审核"),
        str(uuid4()),
        True,
    )
    order_line = sales.detail(f.maker, order)["lines"][0]
    shipment = UUID(
        fulfillment.allocate(
            sales,
            f.maker,
            order,
            ShipmentCreate(
                expected_version="2",
                client_operation_id=uuid4(),
                lines=[{"order_line_id": order_line["id"], "qty": "3"}],
            ),
            str(uuid4()),
        )["id"]
    )
    manifest = sales.detail(f.maker, order)["shipments"][0]
    checks = [
        {"shipment_line_id": line["id"], "qty": line["qty"], "lot_no": line["lot_no"]}
        for line in manifest["lines"]
    ]
    fulfillment.pick_or_verify(
        sales,
        f.maker,
        shipment,
        Pick(expected_version="1", note="拣货", lines=checks),
        str(uuid4()),
        False,
    )
    fulfillment.pick_or_verify(
        sales,
        f.reviewer,
        shipment,
        Verify(expected_version="2", note="复核", shipping_ref="TEST", lines=checks),
        str(uuid4()),
        True,
    )
    posting.post(
        sales,
        f.reviewer,
        shipment,
        ShipmentPost(expected_version="3"),
        str(uuid4()),
    )
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.receivable_obligation") == 60
    retail = RetailService(f.world.uow)
    account = retail.finance.create_account(
        f.maker,
        AccountCreate(entity_id=f.world.entity, code="CASH", name="现金", method="CASH"),
        str(uuid4()),
    )
    rid = UUID(
        retail.create(
            f.maker,
            RetailCreate(
                client_operation_id=uuid4(),
                entity_id=f.world.entity,
                warehouse_id=x.wid,
                lines=[{"sku_id": f.sku, "qty": "2", "unit_price": "20", "tax_rate": "0"}],
            ),
            str(uuid4()),
        )["id"]
    )
    retail_payments.receive(
        retail,
        f.maker,
        rid,
        RetailPayment(
            client_operation_id=uuid4(),
            expected_version="1",
            account_id=account["id"],
            tendered="40",
            confirmed=True,
            reason="现金已收",
        ),
        str(uuid4()),
    )
    doc = retail.detail(f.maker, rid)
    result = retail_posting.complete(
        retail,
        f.maker,
        rid,
        RetailComplete(
            expected_version=doc["sale"]["version"],
            reason="核对交付",
            lines=[
                {
                    "allocation_id": line["id"],
                    "qty": line["allocated_qty"],
                    "lot_no": line["lot_no"],
                }
                for line in doc["allocations"]
            ],
        ),
        str(uuid4()),
    )
    assert result["cost_amount"] == "20.00000000"
    assert (
        f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid)
        == 5
    )
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 50
    assert (
        f.world.scalar(
            "SELECT sum(qty_delta) FROM med.stock_ledger l JOIN med.stock_balance b "
            "ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id) WHERE b.warehouse_id=:w",
            w=x.wid,
        )
        == 5
    )


def test_opening_idempotency_independent_review_and_single_warehouse_activation(opened):
    x, f = opened, opened.f
    request = x.body()
    a = x.service.create(f.maker, request, str(uuid4()))
    assert x.service.create(f.maker, request, str(uuid4())) == a
    oid = UUID(a["id"])
    with pytest.raises(BusinessError) as err:
        x.review(oid, ctx=f.maker)
    assert err.value.code == "MAKER_CHECKER"
    with pytest.raises(BusinessError):
        x.post(oid)
    with pytest.raises(BusinessError):
        x.create()
    x.review(oid)
    with pytest.raises(BusinessError):
        x.post(oid, ctx=f.maker)
    result = x.post(oid)
    assert x.post(oid) == result  # A new key cannot create a second posting.
    with pytest.raises(BusinessError):
        x.create()
    with pytest.raises(BusinessError):
        x.service.transition(f.maker, oid, x.transition(oid), str(uuid4()))
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.posting_batch WHERE source_type='INVENTORY_OPENING'"
        )
        == 1
    )
    for sql in [
        "UPDATE med.inventory_opening SET reason='changed'",
        "UPDATE med.inventory_opening_line SET qty=20",
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql)


@pytest.mark.parametrize(
    "change", ["expired", "frozen", "product_changed", "period_closed", "receipt_arrived"]
)
def test_post_revalidates_eligibility_and_rolls_back(opened, change):
    x, f = opened, opened.f
    oid = x.create()
    x.review(oid)
    if change == "expired":
        f.world.execute("UPDATE med.product SET valid_until=:d", d=f.day - timedelta(days=1))
    elif change == "frozen":
        f.world.execute("UPDATE med.stock_gate SET blocked=true")
    elif change == "product_changed":
        f.world.execute("UPDATE med.product SET name='已修改'")
    elif change == "period_closed":
        f.world.execute(
            "INSERT INTO med.accounting_period(tenant_id,entity_id,period,state) "
            "VALUES(:t,:e,:p,'CLOSED')",
            t=f.maker.tenant_id,
            e=f.world.entity,
            p=f.day.strftime("%Y-%m"),
        )
    else:
        # Arrival entered after the opening draft was approved: opening must now stop.
        f.world.execute(
            "UPDATE med.purchase_order SET warehouse_id=:w WHERE id=:id", w=x.wid, id=f.oid
        )
        f.world.location = x.lid
        f.receive("1")
    with pytest.raises(BusinessError):
        x.post(oid)
    assert x.service.detail(f.maker, oid)["document"]["state"] == "APPROVED"
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.posting_batch WHERE source_type='INVENTORY_OPENING'"
        )
        == 0
    )
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid) == 0
    )
    assert f.world.scalar("SELECT count(*) FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 0


def test_invalid_location_precision_and_existing_warehouse_rejected(opened):
    x, f = opened, opened.f
    for changes in [
        {"location_id": f.world.location},
        {"qty": "0.5"},
        {"expiry_date": f.day - timedelta(days=1)},
    ]:
        line = {**x.body().lines[0].model_dump(), **changes}
        with pytest.raises(BusinessError):
            x.create(x.body(lines=[line]))
    with pytest.raises(BusinessError):
        x.create(x.body(warehouse_id=f.world.warehouse))
    assert f.world.scalar("SELECT count(*) FROM med.inventory_opening") == 0


@pytest.mark.parametrize("conflict", [False, True])
def test_multi_location_cost_pool_and_lot_conflict_are_atomic(opened, conflict):
    x, f = opened, opened.f
    from medinv.modules.inventory.api.warehouses import LocationCreate

    loc = WarehouseService(f.world.uow).add_location(
        f.maker,
        x.wid,
        LocationCreate(
            client_operation_id=uuid4(),
            code="B1",
            name="二号位",
            expected_version="1",
            reason="新增库位",
        ),
        str(uuid4()),
    )
    lines = [x.body().lines[0].model_dump()]
    lines.append({**lines[0], "location_id": UUID(loc["id"]), "qty": "5", "cost_amount": "25"})
    if conflict:
        lines[1]["expiry_date"] += timedelta(days=1)
        oid = x.create(x.body(lines=lines))
        x.review(oid)
        with pytest.raises(BusinessError) as err:
            x.post(oid)
        assert err.value.code == "LOT_IDENTITY_CONFLICT"
        assert (
            f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid)
            == 0
        )
        assert f.world.scalar("SELECT count(*) FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 0
        return
    oid = x.create(x.body(lines=lines))
    x.review(oid)
    x.post(oid)
    assert f.world.scalar("SELECT sum(qty) FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 15
    assert (
        f.world.scalar("SELECT sum(amount) FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 125
    )
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid) == 2
    )


def test_scope_revocation_and_cancel_rebuild(opened):
    x, f = opened, opened.f
    oid = x.create()
    for ctx in [
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
        replace(f.maker, entity_ids=frozenset({uuid4()})),
        replace(f.maker, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            x.review(oid, ctx=ctx)
        with pytest.raises(BusinessError):
            x.post(oid, ctx=ctx)
    x.review(oid, decision="REJECT")
    oid = x.create()
    x.service.transition(f.maker, oid, x.transition(oid), str(uuid4()))
    oid = x.create()
    x.review(oid)

    def revoke():
        raise BusinessError("SESSION_REVOKED", "撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        x.post(oid)
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid) == 0
    )
    assert f.world.scalar("SELECT count(*) FROM med.lot WHERE sku_id=:s", s=f.sku) == 0
    x.post(oid)


def test_competing_posts_have_one_stock_and_cost_effect(opened):
    x, f = opened, opened.f
    oid = x.create()
    x.review(oid)
    request, barrier = x.transition(oid), Barrier(2)

    def attempt(_):
        barrier.wait(timeout=10)
        return x.post(oid, request=request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    assert results[0] == results[1]
    assert (
        f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid)
        == 10
    )
    assert f.world.scalar("SELECT sum(amount_delta) FROM med.cost_ledger") == Decimal(100)


def test_opening_http_permissions_and_default_switch(api, opened, settings):
    x, f = opened, opened.f
    client, owner = api
    path = "/api/v1/inventory-openings"
    choices = client.get(path + "/options", params={"entity_id": str(f.world.entity)})
    assert choices.status_code == 200
    sku = next(s for s in choices.json()["data"]["skus"] if s["id"] == str(f.sku))
    assert sku["qty_scale"] == f.world.scalar(
        "SELECT qty_scale FROM med.sku WHERE id=:id", id=f.sku
    )
    data = x.body().model_dump(mode="json")
    assert client.post(path, json=data).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:p WHERE tenant_id=:t"),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id},
        )
    assert client.post(path, json=data, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    with TestClient(
        create_app(settings.model_copy(update={"opening_commands_enabled": False}))
    ) as disabled:
        disabled.cookies.update(client.cookies)
        assert disabled.post(path, json=data, headers=dict(client.headers)).status_code == 503
    response = client.post(path, json=data)
    assert response.status_code == 200
    assert client.get(path + "/" + response.json()["data"]["id"]).status_code == 200
