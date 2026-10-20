from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_packaging import approved, line
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.entrypoints.core_api import create_app
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.sales.api.schemas import CreditSet, SalesRevision
from medinv.modules.sales.application import amendments, fulfillment
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def body(s, oid=None, **changes):
    doc = s.detail(oid)
    data = {
        k: doc["order"][k]
        for k in (
            "customer_id",
            "warehouse_id",
            "delivery_address",
            "credit_days",
            "minimum_shelf_days",
        )
    }
    return SalesRevision.model_validate(
        {
            **data,
            "expected_version": doc["order"]["version"],
            "reason": "客户确认修改订货约定",
            "lines": [{"sku_id": s.f.sku, "qty": "6", "unit_price": "30", "tax_rate": "0.13"}],
            **changes,
        }
    )


def revise(s, request=None, oid=None, ctx=None, key=None):
    return amendments.revise(
        s.service, ctx or s.f.maker, oid or s.oid, request or body(s, oid), key or str(uuid4())
    )


def approve(s, oid=None, ctx=None):
    return s.service.transition(
        ctx or s.f.reviewer,
        oid or s.oid,
        Transition(expected_version=s.detail(oid)["order"]["version"], reason="独立审核新约定"),
        str(uuid4()),
        True,
    )


def history(s, oid=None, ctx=None, offset=0):
    return amendments.history(s.service, ctx or s.f.maker, oid or s.oid, offset)


def credit(s, customer=None):
    return s.service.credit(s.f.maker, s.f.world.entity, customer or s.customer)


def test_revise_releases_credit_then_reapproval_and_shipment_use_new_agreement(sale):
    s = sale
    before = s.detail()
    original_cost = s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance")
    request, key = body(s), str(uuid4())
    result = revise(s, request, key=key)
    assert revise(s, request, key=key) == result
    assert result["released_exposure"] == "200.00"
    assert credit(s)["exposure"] == "0.00" and credit(s)["receivable"] == "0.00"
    doc = s.detail()
    assert doc["order"]["state"] == "DRAFT" and doc["order"]["revision_no"] == 2
    assert doc["order"]["number"] == before["order"]["number"]
    assert doc["lines"][0]["id"] != before["lines"][0]["id"]
    assert (
        s.f.world.scalar(
            "SELECT count(*) FROM med.inventory_demand_line WHERE demand_id=:id", id=s.oid
        )
        == 0
    )
    assert s.scalar("on_hand") == 10 and s.scalar("reserved") == 0
    with pytest.raises(BusinessError):
        s.allocate("1")
    event = history(s)["items"][0]["detail"]
    assert event["before"]["order"]["gross_amount"] == "200.00"
    assert event["after"]["order"]["gross_amount"] == "180.00"
    approve(s)
    assert credit(s)["exposure"] == "180.00"
    s.post(s.ready("6"))
    assert s.scalar("on_hand") == 4 and credit(s)["receivable"] == "180.00"
    assert credit(s)["exposure"] == "0.00" and not s.detail()["order"]["can_revise"]
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == original_cost * Decimal(
        "0.4"
    )
    assert s.f.world.scalar("SELECT sum(gross_amount) FROM med.receivable_obligation") == 180


def test_draft_multi_editor_separation_and_new_review_round(sale):
    s = sale
    oid = s.order(approve=False)
    second = replace(s.f.maker, principal_id=uuid4())
    independent = replace(s.f.reviewer, principal_id=uuid4())
    revise(s, oid=oid, ctx=s.f.reviewer)
    revise(s, body(s, oid, delivery_address="修正送货地址"), oid=oid, ctx=second)
    for actor in (s.f.maker, s.f.reviewer, second):
        with pytest.raises(BusinessError) as err:
            approve(s, oid, actor)
        assert err.value.code == "SEPARATION_REQUIRED"
    approve(s, oid, independent)
    revise(s, body(s, oid, credit_days=15), oid=oid)
    approve(s, oid)  # Previous round's editor may review a different new round.
    assert s.detail(oid)["order"]["revision_editors"] == []


def test_change_customer_and_warehouse_releases_old_expired_credit(sale):
    s = sale
    customer, warehouse = uuid4(), uuid4()
    for table, old, new in [
        ("party", s.customer, customer),
        ("warehouse", s.f.world.warehouse, warehouse),
    ]:
        s.f.world.execute(
            f"""INSERT INTO med.{table} SELECT
            (jsonb_populate_record(NULL::med.{table},to_jsonb(r)||
             jsonb_build_object('id',CAST(:new AS text),'code','REVISED'))).*
            FROM med.{table} r WHERE id=:old""",
            new=new,
            old=old,
        )
    s.f.world.execute("UPDATE med.party SET state='SUSPENDED' WHERE id=:id", id=s.customer)
    s.f.world.execute("UPDATE med.credit_account SET valid_until=:d", d=s.f.day - timedelta(days=1))
    revise(
        s,
        body(
            s,
            customer_id=customer,
            warehouse_id=warehouse,
            credit_days=7,
            delivery_address="新客户新地址",
            minimum_shelf_days=30,
        ),
    )
    assert credit(s)["exposure"] == "0.00"
    with pytest.raises(BusinessError) as err:
        approve(s)
    assert err.value.code == "CREDIT_NOT_CONFIGURED"
    s.service.set_credit(
        s.f.maker,
        s.f.world.entity,
        customer,
        CreditSet(
            expected_version="0",
            credit_limit="500",
            valid_until=s.f.day + timedelta(days=90),
            reason="新客户授信",
        ),
        str(uuid4()),
    )
    approve(s)
    assert credit(s, customer)["exposure"] == "180.00"
    assert (
        s.f.world.scalar("SELECT warehouse_id FROM med.inventory_demand WHERE id=:id", id=s.oid)
        == warehouse
    )


def test_packaging_revision_preserves_units_and_amount_on_partial_shipment(sale):
    s = sale
    package = approved(s.f)
    revise(s, body(s, lines=[line(s.f, package, qty="2", price="30")]))
    doc = s.detail()
    assert Decimal(doc["lines"][0]["qty"]) == 6
    assert Decimal(doc["lines"][0]["trade_qty"]) == 2
    assert doc["lines"][0]["package_snapshot"]["packaging_id"] == package["id"]
    approve(s)
    s.post(s.ready("3"))
    assert credit(s)["exposure"] == "30.00" and credit(s)["receivable"] == "30.00"
    s.post(s.ready("3"))
    assert credit(s)["exposure"] == "0.00" and credit(s)["receivable"] == "60.00"


@pytest.mark.parametrize("issue", ["unchanged", "quantity", "customer", "warehouse", "frozen"])
def test_invalid_revision_keeps_credit_demand_and_order(sale, issue):
    s = sale
    req = body(s)
    if issue == "unchanged":
        req = body(
            s,
            lines=[
                {"sku_id": s.f.sku, "qty": "10.000000", "unit_price": "20.0", "tax_rate": "0.1300"}
            ],
        )
    if issue == "quantity":
        req = body(
            s, lines=[{"sku_id": s.f.sku, "qty": "1.5", "unit_price": "20", "tax_rate": "0.13"}]
        )
    if issue == "customer":
        req.customer_id = uuid4()
    if issue == "warehouse":
        req.warehouse_id = uuid4()
    if issue == "frozen":
        s.f.world.execute("UPDATE med.stock_gate SET blocked=true")
    before, exposure, events = s.detail(), credit(s), history(s)
    with pytest.raises(BusinessError):
        revise(s, req)
    assert s.detail() == before and credit(s) == exposure and history(s) == events


@pytest.mark.parametrize("stage", ["ALLOCATED", "CANCELLED_SHIPMENT", "POSTED", "CLOSED"])
def test_started_or_closed_sales_cannot_be_revised_and_db_keeps_history(sale, stage):
    s = sale
    if stage == "POSTED":
        s.post(s.ready("1"))
    elif stage in {"ALLOCATED", "CANCELLED_SHIPMENT"}:
        sid = s.allocate("1")["id"]
        if stage == "CANCELLED_SHIPMENT":
            fulfillment.cancel(
                s.service,
                s.f.maker,
                UUID(sid),
                Transition(expected_version="1", reason="归位取消"),
                str(uuid4()),
            )
    else:
        s.service.transition(
            s.f.maker,
            s.oid,
            Transition(expected_version="2", reason="关闭原单"),
            str(uuid4()),
            False,
        )
    before = s.detail()
    with pytest.raises(BusinessError):
        revise(s)
    assert s.detail() == before and not before["order"]["can_revise"]
    for sql in (
        "UPDATE med.sales_order SET state='DRAFT' WHERE id=:id",
        "UPDATE med.sales_order SET customer_snapshot='{}' WHERE id=:id",
        "DELETE FROM med.sales_order_line WHERE order_id=:id",
        "DELETE FROM med.inventory_demand_line WHERE demand_id=:id",
    ):
        with pytest.raises(DBAPIError):
            s.f.world.execute(sql, id=s.oid)


def test_revision_rollback_and_retry_restores_all_original_effects(sale):
    s = sale
    request, key = body(s), str(uuid4())
    before, exposure, events = s.detail(), credit(s), history(s)

    def revoke():
        raise BusinessError("SESSION_REVOKED", "提交前撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        revise(s, request, key=key)
    assert s.detail() == before and credit(s) == exposure and history(s) == events
    revise(s, request, key=key)
    approve(s)
    assert credit(s)["exposure"] == "180.00"


@pytest.mark.parametrize("competitor", ["same_key", "revision", "allocate", "approve"])
def test_revision_serializes_credit_with_allocation_approval_and_retries(sale, competitor):
    s = sale
    oid = s.order(approve=False) if competitor == "approve" else s.oid
    request, key, barrier = body(s, oid), str(uuid4()), Barrier(2)

    def attempt(i):
        barrier.wait(timeout=10)
        try:
            if i == 1 and competitor == "allocate":
                return s.allocate("1", expected=request.expected_version)
            if i == 1 and competitor == "approve":
                return s.service.transition(
                    s.f.reviewer,
                    oid,
                    Transition(expected_version=request.expected_version, reason="同时审核"),
                    str(uuid4()),
                    True,
                )
            return revise(
                s, request, oid=oid, key=key if competitor == "same_key" else str(uuid4())
            )
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    if competitor == "same_key":
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, dict) for r in results) == 1 and "VERSION_CONFLICT" in results
    assert s.f.world.scalar("SELECT exposure FROM med.credit_account") == s.f.world.scalar(
        "SELECT sum(remaining_exposure) FROM med.sales_order"
    )


def test_revision_permissions_scope_and_history(sale):
    s = sale
    for role in ("QUALITY", "VIEWER", "FINANCE"):
        actor = replace(s.f.maker, permissions=ROLE_PERMISSIONS[role])
        if "sales.read" in actor.permissions:
            assert history(s, ctx=actor)["items"]
        with pytest.raises(BusinessError):
            revise(s, ctx=actor)
    for actor in (
        replace(s.f.maker, tenant_id=uuid4()),
        replace(s.f.maker, entity_ids=frozenset()),
    ):
        with pytest.raises(BusinessError):
            revise(s, ctx=actor)
        with pytest.raises(BusinessError):
            history(s, ctx=actor)
    s.f.world.execute("UPDATE med.legal_entity SET status='SUSPENDED'")
    with pytest.raises(BusinessError):
        revise(s)


def test_revision_api_csrf_feature_flag_and_history(api, sale, settings):
    s = sale
    client, owner = api
    path = f"/api/v1/sales-orders/{s.oid}"
    request = body(s).model_dump(mode="json")
    assert client.post(path + "/revise", json=request).status_code == 403
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
    assert (
        client.post(path + "/revise", json=request, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.post(path + "/revise", json={**request, "entity_id": str(uuid4())}).status_code
        == 422
    )
    with TestClient(
        create_app(settings.model_copy(update={"sales_commands_enabled": False}))
    ) as disabled:
        disabled.cookies.update(client.cookies)
        assert (
            disabled.post(path + "/revise", json=request, headers=dict(client.headers)).status_code
            == 503
        )
    assert client.post(path + "/revise", json=request).status_code == 200
    assert client.get(path + "/history").json()["data"]["items"][0]["action"] == "sales.revised"
    assert client.get(path + "/history?offset=-1").status_code == 422
