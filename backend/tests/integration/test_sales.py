import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from test_api import api as api
from test_api import validate_response
from test_procurement import flow as flow

from medinv.modules.procurement.api.schemas import Transition
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
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def sale(flow):
    f = flow
    f.post(f.inspect(f.receive()["id"]))
    f.world.execute("UPDATE med.party SET kind='BOTH' WHERE id=:id", id=UUID(f.party["id"]))
    service = SalesService(f.world.uow)
    customer = UUID(f.party["id"])
    service.set_credit(
        f.maker,
        f.world.entity,
        customer,
        CreditSet(
            expected_version="0",
            credit_limit="1000",
            valid_until=f.day + timedelta(days=200),
            reason="测试信用授权",
        ),
        str(uuid4()),
    )

    def order(qty="10", price="20", approve=True, minimum=0, lines=None):
        result = service.create(
            f.maker,
            SalesCreate(
                entity_id=f.world.entity,
                customer_id=customer,
                warehouse_id=f.world.warehouse,
                delivery_address="仅供测试的收货地址",
                minimum_shelf_days=minimum,
                lines=lines
                or [{"sku_id": f.sku, "qty": qty, "unit_price": price, "tax_rate": "0.13"}],
            ),
            str(uuid4()),
        )
        oid = UUID(result["id"])
        if approve:
            service.transition(
                f.reviewer,
                oid,
                Transition(expected_version="1", reason="审核通过"),
                str(uuid4()),
                True,
            )
        return oid

    oid = order()

    def detail(target=None):
        return service.detail(f.maker, target or oid)

    def allocate(qty="10", target=None, op=None, expected=None):
        target = target or oid
        doc = detail(target)
        return fulfillment.allocate(
            service,
            f.maker,
            target,
            ShipmentCreate(
                expected_version=expected or doc["order"]["version"],
                client_operation_id=op or uuid4(),
                lines=[{"order_line_id": doc["lines"][0]["id"], "qty": qty}],
            ),
            str(uuid4()),
        )

    def manifest(sid, target=None):
        return next(s for s in detail(target)["shipments"] if s["id"] == str(sid))

    def pick(sid, target=None):
        sh = manifest(sid, target)
        return fulfillment.pick_or_verify(
            service,
            f.maker,
            UUID(str(sid)),
            Pick(
                expected_version=sh["version"],
                note="逐行实物拣货",
                lines=[
                    {
                        "shipment_line_id": entry["id"],
                        "qty": entry["qty"],
                        "lot_no": entry["lot_no"],
                    }
                    for entry in sh["lines"]
                ],
            ),
            str(uuid4()),
            False,
        )

    def verify(sid, target=None, actor=None):
        sh = manifest(sid, target)
        return fulfillment.pick_or_verify(
            service,
            actor or f.reviewer,
            UUID(str(sid)),
            Verify(
                expected_version=sh["version"],
                note="逐行独立核验",
                shipping_ref="DEMO-WAYBILL",
                lines=[
                    {
                        "shipment_line_id": entry["id"],
                        "qty": entry["qty"],
                        "lot_no": entry["lot_no"],
                    }
                    for entry in sh["lines"]
                ],
            ),
            str(uuid4()),
            True,
        )

    def post(sid):
        return posting.post(
            service, f.reviewer, UUID(str(sid)), ShipmentPost(expected_version="3"), str(uuid4())
        )

    def ready(qty="10", target=None):
        sid = allocate(qty, target)["id"]
        pick(sid, target)
        verify(sid, target)
        return sid

    def scalar(column):
        return f.world.scalar(f"SELECT {column} FROM med.stock_balance WHERE sku_id=:id", id=f.sku)

    return SimpleNamespace(**locals())


def test_partial_shipment_consumes_stock_cost_credit_and_ar_atomically(sale):
    s = sale
    assert s.service.credit(s.f.maker, s.f.world.entity, s.customer)["exposure"] == "200.00"
    sid = s.ready("3")
    assert s.scalar("on_hand") == 10 and s.scalar("reserved") == 3
    result = s.post(sid)
    assert result["posted_document_id"] == sid
    assert s.scalar("on_hand") == 7 and s.scalar("reserved") == 0
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("80.528")
    credit = s.service.credit(s.f.maker, s.f.world.entity, s.customer)
    assert credit["exposure"] == "140.00" and credit["receivable"] == "60.00"
    ar = s.service.receivables(s.f.maker, s.f.world.entity, 0)["items"][0]
    assert ar["net_amount"] == "53.10" and ar["cost_amount"] == "34.51200000"
    s.post(s.ready("7"))
    assert s.scalar("on_hand") == 0
    assert s.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 0
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == 0
    assert s.f.world.scalar("SELECT sum(net_amount) FROM med.receivable_obligation") == Decimal(
        "176.99"
    )
    assert s.detail()["order"]["state"] == "FULFILLED"
    assert s.service.credit(s.f.maker, s.f.world.entity, s.customer)["exposure"] == "0.00"


def test_credit_approval_race_cannot_overcommit_customer(sale):
    s = sale
    s.f.world.execute("UPDATE med.credit_account SET credit_limit=300")
    ids = [s.order("4", approve=False) for _ in range(2)]

    def approve(oid):
        try:
            return s.service.transition(
                s.f.reviewer,
                oid,
                Transition(expected_version="1", reason="审核"),
                str(uuid4()),
                True,
            )["state"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, ids))
    assert sorted(results) == ["APPROVED", "CREDIT_LIMIT_EXCEEDED"]
    assert s.f.world.scalar("SELECT exposure FROM med.credit_account") == 280


def test_concurrent_allocation_and_permanent_duplicate_key(sale):
    s = sale
    op = uuid4()
    version = s.detail()["order"]["version"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: s.allocate("6", op=op, expected=version), range(6)))
    assert len({r["id"] for r in results}) == 1
    assert s.scalar("reserved") == 6
    assert s.f.world.scalar("SELECT count(*) FROM med.shipment") == 1
    with pytest.raises(BusinessError) as err:
        s.allocate("5")
    assert err.value.code == "DEMAND_EXCEEDED"


def test_two_orders_cannot_reserve_the_same_stock(sale):
    s = sale
    second = s.order("10")

    def attempt(oid):
        try:
            s.allocate("7", target=oid)
            return "OK"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [s.oid, second]))
    assert sorted(results) == ["INSUFFICIENT_STOCK", "OK"]
    assert s.scalar("reserved") == 7


def test_concurrent_post_retry_creates_only_one_ar_and_issue(sale):
    s = sale
    sid = s.ready("4")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: s.post(sid), range(6)))
    assert len({r["posting_id"] for r in results}) == 1
    assert s.scalar("on_hand") == 6
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 1
    assert s.f.world.scalar("SELECT count(*) FROM med.stock_ledger WHERE qty_delta<0") == 1


def test_maker_checker_and_actual_batch_quantity_are_required(sale):
    s = sale
    draft = s.order("1", approve=False)
    with pytest.raises(BusinessError) as err:
        s.service.transition(
            s.f.maker, draft, Transition(expected_version="1", reason="不允许"), str(uuid4()), True
        )
    assert err.value.code == "SEPARATION_REQUIRED"
    sid = s.allocate("3")["id"]
    sh = s.manifest(sid)
    with pytest.raises(BusinessError) as err:
        fulfillment.pick_or_verify(
            s.service,
            s.f.maker,
            UUID(sid),
            Pick(
                expected_version="1",
                note="错误批号",
                lines=[{"shipment_line_id": sh["lines"][0]["id"], "qty": "3", "lot_no": "WRONG"}],
            ),
            str(uuid4()),
            False,
        )
    assert err.value.code == "PICK_MISMATCH"
    s.pick(sid)
    with pytest.raises(BusinessError) as err:
        s.verify(sid, actor=s.f.maker)
    assert err.value.code == "SEPARATION_REQUIRED"
    with pytest.raises(BusinessError):
        s.post(sid)
    assert s.scalar("on_hand") == 10


@pytest.mark.parametrize(
    "change,code",
    [
        ("UPDATE med.lot SET quality_blocked=true", "SHIPMENT_INELIGIBLE"),
        (
            "UPDATE med.lot SET expiry_exclusive=clock_timestamp()-interval '1 second'",
            "SHIPMENT_INELIGIBLE",
        ),
        (
            "UPDATE med.reservation SET expires_at=clock_timestamp()-interval '1 second'",
            "RESERVATION_EXPIRED",
        ),
        ("UPDATE med.party SET state='SUSPENDED'", "CUSTOMER_INELIGIBLE"),
        ("UPDATE med.product SET approval_state='SUSPENDED'", "PRODUCT_INELIGIBLE"),
        ("UPDATE med.credit_account SET valid_until=current_date-1", "CREDIT_EXPIRED"),
        ("UPDATE med.cost_balance SET qty=0,amount=0", "COST_BASIS_MISSING"),
    ],
)
def test_changed_conditions_after_verification_block_issue(sale, change, code):
    s = sale
    sid = s.ready("3")
    s.f.world.execute(change)
    with pytest.raises(BusinessError) as err:
        s.post(sid)
    assert err.value.code == code
    assert s.scalar("on_hand") == 10 and s.scalar("reserved") == 3
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0


def test_cancel_expired_reservation_and_close_partial_order(sale):
    s = sale
    s.post(s.ready("3"))
    sid = s.ready("2")
    s.f.world.execute("UPDATE med.reservation SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(BusinessError):
        s.service.transition(
            s.f.maker,
            s.oid,
            Transition(expected_version=s.detail()["order"]["version"], reason="关闭余量"),
            str(uuid4()),
            False,
        )
    fulfillment.cancel(
        s.service,
        s.f.maker,
        UUID(sid),
        Transition(expected_version="3", reason="超时释放"),
        str(uuid4()),
    )
    assert s.scalar("reserved") == 0
    s.service.transition(
        s.f.maker,
        s.oid,
        Transition(expected_version=s.detail()["order"]["version"], reason="关闭余量"),
        str(uuid4()),
        False,
    )
    credit = s.service.credit(s.f.maker, s.f.world.entity, s.customer)
    assert credit["exposure"] == "0.00" and credit["receivable"] == "60.00"
    assert s.detail()["order"]["state"] == "CLOSED"
    assert s.scalar("on_hand") == 7


def test_minimum_shelf_life_filters_fefo_candidates(sale):
    s = sale
    oid = s.order("2", minimum=400)
    with pytest.raises(BusinessError) as err:
        s.allocate("2", oid)
    assert err.value.code == "INSUFFICIENT_STOCK"
    assert s.scalar("reserved") == 0


def test_authorization_probe_failure_rolls_back_complete_sales_post(sale):
    s = sale
    sid = s.ready("4")

    def fail():
        raise RuntimeError("lost control connection")

    marker = before_business_commit.set(fail)
    try:
        with pytest.raises(RuntimeError):
            s.post(sid)
    finally:
        before_business_commit.reset(marker)
    assert s.scalar("on_hand") == 10 and s.scalar("reserved") == 4
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("115.04")
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert s.manifest(sid)["state"] == "VERIFIED"
    s.post(sid)


def test_sales_tenant_entity_and_viewer_boundaries(sale):
    s = sale
    viewer = replace(s.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    with pytest.raises(BusinessError):
        s.service.transition(
            viewer, s.oid, Transition(expected_version="2", reason="越权"), str(uuid4()), False
        )
    with pytest.raises(BusinessError):
        s.service.detail(replace(viewer, tenant_id=uuid4()), s.oid)
    with pytest.raises(BusinessError):
        s.service.detail(replace(viewer, entity_ids=frozenset()), s.oid)
    with pytest.raises(BusinessError):
        s.service.set_credit(
            viewer,
            s.f.world.entity,
            s.customer,
            CreditSet(
                expected_version="2", credit_limit="1000", valid_until=s.f.day, reason="越权"
            ),
            str(uuid4()),
        )


def test_small_split_amounts_keep_tax_nonnegative_and_sum_to_order(sale):
    s = sale
    oid = s.order("3", price="0.003333")
    for _ in range(3):
        s.post(s.ready("1", oid))
    ar = s.service.receivables(s.f.maker, s.f.world.entity, 0)["items"]
    assert len(ar) == 3
    assert sum(Decimal(r["gross_amount"]) for r in ar) == Decimal("0.01")
    assert sum(Decimal(r["net_amount"]) for r in ar) == Decimal("0.01")
    assert all(Decimal(r["tax_amount"]) >= 0 for r in ar)
    assert s.detail(oid)["order"]["state"] == "FULFILLED"


def test_two_order_lines_consume_same_cost_pool_without_losing_updates(sale):
    s = sale
    oid = s.order(
        lines=[
            {"sku_id": s.f.sku, "qty": "3", "unit_price": "20", "tax_rate": "0.13"},
            {"sku_id": s.f.sku, "qty": "7", "unit_price": "21", "tax_rate": "0.13"},
        ]
    )
    doc = s.detail(oid)
    result = fulfillment.allocate(
        s.service,
        s.f.maker,
        oid,
        ShipmentCreate(
            expected_version=doc["order"]["version"],
            client_operation_id=uuid4(),
            lines=[{"order_line_id": entry["id"], "qty": entry["qty"]} for entry in doc["lines"]],
        ),
        str(uuid4()),
    )
    s.pick(result["id"], oid)
    s.verify(result["id"], oid)
    s.post(result["id"])
    assert s.scalar("on_hand") == 0 and s.scalar("reserved") == 0
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == 0
    ar = s.service.receivables(s.f.maker, s.f.world.entity, 0)["items"][0]
    assert Decimal(ar["gross_amount"]) == 207
    assert Decimal(ar["cost_amount"]) == Decimal("115.04")
    assert s.detail(oid)["order"]["state"] == "FULFILLED"


def test_fefo_skips_insufficient_shelf_life_and_selects_earliest_eligible(sale):
    s = sale
    s.f.world.add_balance("3", days=10, sku=s.f.sku)
    eligible, _ = s.f.world.add_balance("3", days=40, sku=s.f.sku)
    oid = s.order("2", minimum=30)
    sid = s.allocate("2", oid)["id"]
    assert s.manifest(sid, oid)["lines"][0]["lot_no"] == str(eligible)


def test_closed_period_blocks_sales_post_without_financial_side_effects(sale):
    s = sale
    sid = s.ready("2")
    # The runtime role intentionally cannot close periods. Arrange this fixture as the owner.
    owner = create_engine(os.environ["MED_MIGRATION_URL"], hide_parameters=True)
    try:
        with owner.begin() as conn:
            conn.execute(text("SET ROLE med_owner"))
            conn.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(s.f.maker.tenant_id)}
            )
            conn.execute(
                text("UPDATE med.accounting_period SET state='CLOSED' WHERE entity_id=:e"),
                {"e": s.f.world.entity},
            )
    finally:
        owner.dispose()
    with pytest.raises(BusinessError) as err:
        s.post(sid)
    assert err.value.code == "PERIOD_CLOSED"
    assert s.scalar("on_hand") == 10 and s.scalar("reserved") == 2
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0


def test_overdue_receivable_blocks_further_approval(sale, monkeypatch):
    s = sale
    s.post(s.ready("1"))
    oid = s.order("1", approve=False)
    real_now = s.service.now
    monkeypatch.setattr(s.service, "now", lambda repo: real_now(repo) + timedelta(days=31))
    with pytest.raises(BusinessError) as err:
        s.service.transition(
            s.f.reviewer, oid, Transition(expected_version="1", reason="审核"), str(uuid4()), True
        )
    assert err.value.code == "CREDIT_OVERDUE"
    assert s.detail(oid)["order"]["state"] == "DRAFT"


def test_sales_post_http_response_matches_contract_and_retry_is_stable(sale, api):
    s = sale
    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=:perms
        WHERE tenant_id=:t AND principal_id=:p"""),
            {
                "perms": list(ROLE_PERMISSIONS["OWNER"]),
                "t": s.f.maker.tenant_id,
                "p": s.f.maker.principal_id,
            },
        )
    sid = s.ready("2")
    response = client.post(f"/api/v1/shipments/{sid}/post", json={"expected_version": "3"})
    assert response.status_code == 200, response.json()
    validate_response(response, "PostingResultResponse")
    client.headers["Idempotency-Key"] = str(uuid4())
    again = client.post(f"/api/v1/shipments/{sid}/post", json={"expected_version": "3"})
    assert again.status_code == 200
    assert again.json()["data"] == response.json()["data"]
