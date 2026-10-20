from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.procurement.api.schemas import InspectionCreate, ReceiptCreate, Transition
from medinv.modules.sales.api.schemas import ReplenishmentCreate
from medinv.modules.sales.application.replenishment import ReplenishmentService
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def setup(s, qty="15", **kwargs):
    return ReplenishmentService(s.f.world.uow), s.order(qty, **kwargs)


def body(s, qty="5", **kwargs):
    return ReplenishmentCreate(
        expected_version="2",
        supplier_id=s.customer,
        lines=[{"sku_id": s.f.sku, "qty": qty, "unit_price": "13", "tax_rate": "0.13"}],
        **kwargs,
    )


def item(service, s, oid):
    return service.detail(s.f.maker, oid)["items"][0]


def procure(s, pid, qty="5", qualified="5", rejected="0", post=True):
    f = s.f
    target = UUID(pid)
    f.service.transition(
        f.reviewer,
        target,
        Transition(expected_version="1", reason="独立补货审核"),
        str(uuid4()),
        True,
    )
    doc = f.service.detail(f.maker, target)
    received = f.service.receive(
        f.maker,
        target,
        ReceiptCreate(
            expected_version="2",
            shipment_ref="REPLENISH",
            transport_note="常温资料核对",
            lines=[
                {
                    "order_line_id": doc["lines"][0]["id"],
                    "location_id": f.world.location,
                    "qty": qty,
                    "lot_no": "REPLENISH",
                    "mfg_date": f.day - timedelta(days=1),
                    "expiry_date": f.day + timedelta(days=400),
                }
            ],
        ),
        str(uuid4()),
    )
    rec = f.service.detail(f.maker, target)["receipts"][0]
    inspected = f.service.inspect(
        f.reviewer,
        UUID(received["id"]),
        InspectionCreate(
            expected_version=rec["version"],
            note="补货独立验收",
            lines=[
                {
                    "receipt_line_id": rec["lines"][0]["id"],
                    "qualified_qty": qualified,
                    "rejected_qty": rejected,
                }
            ],
        ),
        str(uuid4()),
    )
    if post:
        f.post(inspected)
    return inspected


def test_replenishment_links_original_purchase_without_stock_or_payable_mutation(sale):
    s = sale
    service, oid = setup(s)
    before = s.f.world.scalar("SELECT count(*) FROM med.audit_event")
    row = item(service, s, oid)
    assert Decimal(row["remaining_qty"]) == 15
    assert Decimal(row["available_qty"]) == 10
    assert Decimal(row["suggested_qty"]) == 5
    assert s.f.world.scalar("SELECT count(*) FROM med.audit_event") == before
    key = str(uuid4())
    created = service.create(s.f.maker, oid, body(s, "2"), key)
    assert service.create(s.f.maker, oid, body(s, "2"), key) == created
    doc = s.f.service.detail(s.f.maker, UUID(created["id"]))
    assert doc["order"]["source_sales_order_id"] == str(oid)
    assert doc["order"]["source_sales_version"] == 2
    assert doc["order"]["source_sales_number"] == s.detail(oid)["order"]["number"]
    assert Decimal(item(service, s, oid)["pending_purchase_qty"]) == 2
    assert Decimal(item(service, s, oid)["suggested_qty"]) == 3
    assert s.scalar("on_hand") == 10
    assert s.f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 130
    with pytest.raises(BusinessError) as error:
        s.f.service.transition(
            s.f.maker,
            UUID(created["id"]),
            Transition(expected_version="1", reason="本人不能审核"),
            str(uuid4()),
            True,
        )
    assert error.value.code == "SEPARATION_REQUIRED"


def test_replenishment_same_sku_lines_share_stock_once(sale):
    s = sale
    service, oid = setup(
        s, lines=[{"sku_id": s.f.sku, "qty": q, "unit_price": "20"} for q in ("8", "7")]
    )
    data = service.detail(s.f.maker, oid)
    assert len(data["items"]) == 1
    assert Decimal(data["items"][0]["suggested_qty"]) == 5


def test_replenishment_receipt_rejection_and_remainder_close_update_pending(sale):
    s = sale
    service, oid = setup(s)
    pid = service.create(s.f.maker, oid, body(s), str(uuid4()))["id"]
    inspected = procure(s, pid, "3", "1", "1", post=False)
    row = item(service, s, oid)
    assert Decimal(row["pending_purchase_qty"]) == 4  # 2 unreceived + 1 pending + 1 qualified
    assert Decimal(row["suggested_qty"]) == 1  # rejected quantity no longer covers demand
    s.f.post(inspected)
    row = item(service, s, oid)
    assert Decimal(row["available_qty"]) == 11 and Decimal(row["pending_purchase_qty"]) == 3
    doc = s.f.service.detail(s.f.maker, UUID(pid))
    s.f.service.close(
        s.f.maker,
        UUID(pid),
        Transition(expected_version=doc["order"]["version"], reason="关闭未收两件"),
        str(uuid4()),
    )
    row = item(service, s, oid)
    assert Decimal(row["pending_purchase_qty"]) == 1 and Decimal(row["suggested_qty"]) == 3


def test_replenishment_arrival_then_original_sales_fulfillment(sale):
    s = sale
    service, oid = setup(s)
    pid = service.create(s.f.maker, oid, body(s), str(uuid4()))["id"]
    procure(s, pid)
    row = item(service, s, oid)
    assert Decimal(row["available_qty"]) == 15
    assert Decimal(row["pending_purchase_qty"]) == Decimal(row["suggested_qty"]) == 0
    s.post(s.ready("15", oid))
    assert s.detail(oid)["order"]["state"] == "FULFILLED"
    assert Decimal(item(service, s, oid)["remaining_qty"]) == 0
    assert (
        s.f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:id", id=s.f.sku)
        == 0
    )
    assert s.f.world.scalar("SELECT sum(gross_amount) FROM med.receivable_obligation") == 300
    assert s.f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 195
    assert (
        s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance WHERE sku_id=:id", id=s.f.sku)
        == 0
    )


def test_partial_allocation_does_not_replenish_already_reserved_goods(sale):
    s = sale
    service, oid = setup(s)
    s.allocate("10", oid)
    row = item(service, s, oid)
    assert Decimal(row["remaining_qty"]) == 5
    assert Decimal(row["available_qty"]) == 0 and Decimal(row["suggested_qty"]) == 5


def test_cancelled_purchase_releases_suggestion_and_concurrent_creation_cannot_duplicate(sale):
    s = sale
    service, oid = setup(s)

    def create():
        try:
            return service.create(s.f.maker, oid, body(s), str(uuid4()))
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: create(), range(2)))
    assert outcomes.count("REPLENISHMENT_CHANGED") == 1
    created = next(r for r in outcomes if isinstance(r, dict))
    assert len(service.detail(s.f.maker, oid)["purchases"]) == 1
    s.f.service.transition(
        s.f.maker,
        UUID(created["id"]),
        Transition(expected_version="1", reason="取消采购草稿"),
        str(uuid4()),
        False,
    )
    assert Decimal(item(service, s, oid)["suggested_qty"]) == 5


@pytest.mark.parametrize("change", ["stock", "gate", "version", "state", "sku", "supplier"])
def test_creation_rechecks_current_demand_stock_and_scope(sale, change):
    s = sale
    service, oid = setup(s)
    payload = body(s)
    if change == "stock":
        s.f.world.execute("UPDATE med.stock_balance SET on_hand=15 WHERE sku_id=:id", id=s.f.sku)
    elif change == "gate":
        s.f.world.execute("UPDATE med.stock_gate SET blocked=true WHERE sku_id=:id", id=s.f.sku)
    elif change == "version":
        payload = payload.model_copy(update={"expected_version": "1"})
    elif change == "state":
        s.service.transition(
            s.f.maker, oid, Transition(expected_version="2", reason="关闭需求"), str(uuid4()), False
        )
        payload = payload.model_copy(update={"expected_version": "3"})
    elif change == "sku":
        payload.lines[0].sku_id = s.f.world.sku
    else:
        payload = payload.model_copy(update={"supplier_id": uuid4()})
    with pytest.raises(BusinessError):
        service.create(s.f.maker, oid, payload, str(uuid4()))
    assert service.detail(s.f.maker, oid)["purchases"] == []


def test_permissions_tenant_scope_source_immutability_and_commit_rollback(sale):
    s = sale
    service, oid = setup(s)
    for permission in ("sales.read", "purchase.read", "purchase.write"):
        actor = replace(s.f.maker, permissions=s.f.maker.permissions - {permission})
        with pytest.raises(BusinessError):
            service.create(actor, oid, body(s), str(uuid4()))
        if permission != "purchase.write":
            with pytest.raises(BusinessError):
                service.detail(actor, oid)
    for actor in (
        replace(s.f.maker, entity_ids=frozenset()),
        replace(s.f.maker, tenant_id=uuid4()),
    ):
        with pytest.raises(BusinessError):
            service.detail(actor, oid)

    def revoked():
        raise BusinessError("PERMISSION_CHANGED", "提交前撤权", 403)

    token = before_business_commit.set(revoked)
    try:
        with pytest.raises(BusinessError):
            service.create(s.f.maker, oid, body(s), str(uuid4()))
    finally:
        before_business_commit.reset(token)
    assert service.detail(s.f.maker, oid)["purchases"] == []
    created = service.create(s.f.maker, oid, body(s), str(uuid4()))
    with pytest.raises(DBAPIError):
        s.f.world.execute(
            "UPDATE med.purchase_order SET source_sales_order_id=NULL,"
            "source_sales_version=NULL WHERE id=:id",
            id=UUID(created["id"]),
        )


def test_http_contract_and_input_guards(api, sale):
    client, owner = api
    s = sale
    service, oid = setup(s)
    path = f"/api/v1/sales-orders/{oid}/replenishment"
    assert client.get(path).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=:p "
                "WHERE tenant_id=:t AND principal_id=:a"
            ),
            {
                "p": list(s.f.maker.permissions),
                "t": s.f.maker.tenant_id,
                "a": s.f.maker.principal_id,
            },
        )
    response = client.get(path)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    payload = body(s)
    assert client.post(path, json=payload.model_dump(mode="json")).status_code == 200
    bad = payload.model_dump(mode="json")
    bad["lines"] *= 2
    assert client.post(path, json=bad).status_code == 422
    with pytest.raises(ValidationError):
        ReplenishmentCreate.model_validate(
            {**bad, "lines": [{**bad["lines"][0], "packaging_id": str(uuid4())}]}
        )
