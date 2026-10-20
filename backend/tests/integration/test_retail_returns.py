import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_retail import retail as retail

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailCashConfirmation
from medinv.modules.returns.api.schemas import (
    CustodyHandover,
    ReturnCreate,
    ReturnReceive,
    ReturnReview,
)
from medinv.modules.returns.application import custody, posting, retail_refunds
from medinv.modules.returns.application.service import ReturnService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def retail_return(retail):
    r = retail
    f = r.f
    world = f.world
    sid = r.create()["id"]
    r.pay(sid)
    original = r.complete(sid)
    pid = UUID(original["posting_id"])
    service = ReturnService(world.uow)

    def source():
        return service.source_detail(f.maker, pid)

    def create(qty="1", body=None):
        return UUID(
            service.create(
                f.maker,
                body
                or ReturnCreate(
                    client_operation_id=uuid4(),
                    source_posting_id=pid,
                    reason="顾客原单退货",
                    lines=[{"source_posting_line_id": source()["lines"][0]["id"], "qty": qty}],
                ),
                str(uuid4()),
            )["id"]
        )

    def detail(rid):
        return service.detail(f.maker, rid)

    def checks(rid):
        return [
            {
                "source_posting_line_id": line["id"],
                "qty": line["base_qty"],
                "lot_no": line["lot_no"],
            }
            for line in detail(rid)["lines"]
        ]

    def receive(rid):
        return custody.receive(
            service,
            f.maker,
            rid,
            ReturnReceive(
                expected_version=detail(rid)["document"]["version"],
                reason="实际收取退回品",
                zone_label="门店退货隔离区",
                confirmed=True,
                lines=checks(rid),
            ),
            str(uuid4()),
        )

    def review(rid, decision="APPROVE", ctx=None):
        return service.review(
            ctx or f.reviewer,
            rid,
            ReturnReview(
                expected_version=detail(rid)["document"]["version"],
                reason="质量独立核查",
                decision=decision,
                lines=checks(rid) if decision == "APPROVE" else [],
            ),
            str(uuid4()),
        )

    def post(rid):
        return posting.post(
            service,
            f.reviewer,
            rid,
            Transition(
                expected_version=detail(rid)["document"]["version"], reason="核对本次退货入库"
            ),
            str(uuid4()),
        )

    def ready(qty="1"):
        rid = create(qty)
        receive(rid)
        review(rid)
        return rid

    def refund_body(rid):
        return RetailCashConfirmation(
            client_operation_id=uuid4(),
            expected_version=detail(rid)["document"]["version"],
            confirmed=True,
            reason="现金已实际退还",
        )

    def refund(rid, body=None, ctx=None):
        return retail_refunds.create(
            service, ctx or f.maker, rid, body or refund_body(rid), str(uuid4())
        )

    def stock():
        return world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=f.sku)

    return SimpleNamespace(**locals())


def test_partial_retail_returns_restore_original_cost_then_refund_original_cash(retail_return):
    x = retail_return
    assert x.stock() == 7
    rows = x.service.sources(x.f.maker, x.world.entity, "RETAIL", 0)["items"]
    assert len(rows) == 1 and rows[0]["source_number"] == x.r.detail(x.sid)["sale"]["number"]
    for qty, amount, cost in [("1", "20.00", "11.50400000"), ("2", "40.00", "23.00800000")]:
        rid = x.ready(qty)
        doc = x.detail(rid)["document"]
        assert (
            doc["party_id"] is None and doc["receivable_id"] is None and doc["payable_id"] is None
        )
        assert doc["retail_sale_id"] == x.sid and doc["kind"] == "RETAIL"
        assert doc["gross_amount"] == amount
        first = x.post(rid)
        assert x.post(rid) == first
        assert first["cost_amount"] == cost
        before = x.r.detail(x.sid)["returns_summary"]
        assert before["pending_refund_amount"] == amount
        result = x.refund(rid, ctx=replace(x.f.maker, permissions=ROLE_PERMISSIONS["FINANCE"]))
        assert result["amount"] == amount
        saved = retail_refunds.detail(x.service, x.f.maker, rid)
        assert saved["remaining_amount"] == "0.00"
        assert saved["refund"]["account_id"] == x.r.account["id"]
    assert x.stock() == 10
    assert x.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("115.04")
    assert x.world.scalar("SELECT count(*) FROM med.retail_payment") == 1
    assert x.world.scalar("SELECT sum(amount) FROM med.retail_return_refund") == 60
    assert x.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert x.world.scalar("SELECT count(*) FROM med.party") == 1
    assert x.r.detail(x.sid)["sale"]["state"] == "COMPLETED"
    assert x.r.detail(x.sid)["returns_summary"] == {
        "returned_amount": "60.00",
        "refunded_amount": "60.00",
        "pending_refund_amount": "0.00",
    }
    assert x.source()["lines"][0]["remaining_qty"] == "0.000000"
    with pytest.raises(BusinessError):
        x.create()


def test_retail_return_requires_custody_and_independent_review_before_post_and_refund(
    retail_return,
):
    x = retail_return
    rid = x.create()
    with pytest.raises(BusinessError) as err:
        x.review(rid)
    assert err.value.code == "INVALID_STATE"
    with pytest.raises(BusinessError):
        x.post(rid)
    with pytest.raises(BusinessError) as err:
        x.refund(rid)
    assert err.value.code == "REFUND_STATE"
    x.receive(rid)
    assert x.stock() == 7
    assert x.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("80.528")
    with pytest.raises(BusinessError) as err:
        x.review(rid, ctx=x.f.maker)
    assert err.value.code == "MAKER_CHECKER"
    x.review(rid)
    with pytest.raises(BusinessError):
        x.refund(rid)
    assert x.stock() == 7
    x.post(rid)
    assert x.stock() == 8


def test_rejected_retail_goods_are_held_until_handback_without_refund_or_stock(retail_return):
    x = retail_return
    rid = x.create()
    x.receive(rid)
    x.review(rid, "REJECT")
    assert x.detail(rid)["document"]["state"] == "REJECTED"
    held = x.service.listing(x.f.maker, x.world.entity, 0, True)["items"]
    assert [r["id"] for r in held] == [str(rid)]
    with pytest.raises(BusinessError):
        x.refund(rid)
    custody.hand_back(
        x.service,
        x.f.maker,
        rid,
        CustodyHandover(expected_version="3", confirmed=True, reason="全部实物已交还顾客"),
        str(uuid4()),
    )
    assert x.detail(rid)["custody"][0]["outcome"] == "HANDED_BACK"
    assert x.stock() == 7
    assert x.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 0
    assert x.source()["lines"][0]["remaining_qty"] == "3.000000"
    assert x.service.listing(x.f.maker, x.world.entity, 0, True)["items"] == []


@pytest.mark.parametrize("change", ["lot", "product"])
def test_ineligible_return_cannot_restore_available_stock(retail_return, change):
    x = retail_return
    rid = x.ready()
    if change == "lot":
        x.world.execute("UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s", s=x.f.sku)
    else:
        x.world.execute(
            "UPDATE med.product SET approval_state='SUSPENDED' WHERE id=:id",
            id=UUID(x.f.product["id"]),
        )
    with pytest.raises(BusinessError):
        x.post(rid)
    assert x.stock() == 7
    assert x.detail(rid)["custody"][0]["outcome"] is None
    assert x.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 0


def test_return_quantities_and_active_application_block_over_return(retail_return):
    x = retail_return
    with pytest.raises(BusinessError) as err:
        x.create("4")
    assert err.value.code == "RETURN_QUANTITY_EXCEEDED"
    assert x.world.scalar("SELECT count(*) FROM med.return_document") == 0

    def create(_):
        try:
            return x.create()
        except BusinessError as err:
            return err.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert results.count("RETURN_EXISTS") == 1
    rid = next(v for v in results if isinstance(v, UUID))
    x.receive(rid)
    x.review(rid)
    x.post(rid)
    with pytest.raises(BusinessError) as err:
        x.create("3")
    assert err.value.code == "RETURN_QUANTITY_EXCEEDED"


def test_refund_permanent_retry_and_duplicate_operations_write_once(retail_return):
    x = retail_return
    rid = x.ready()
    x.post(rid)
    body = x.refund_body(rid)

    def refund(_):
        return x.refund(rid, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(refund, range(2)))
    assert result[0] == result[1]
    assert x.refund(rid, body) == result[0]
    with pytest.raises(BusinessError) as err:
        x.refund(rid)
    assert err.value.code == "REFUND_EXISTS"
    with pytest.raises(BusinessError) as err:
        x.refund(rid, body.model_copy(update={"reason": "different"}))
    assert err.value.code == "OPERATION_CONFLICT"
    assert x.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 1


@pytest.mark.parametrize("stage", ["post", "refund"])
def test_authorization_failure_rolls_back_all_return_or_refund_effects(retail_return, stage):
    x = retail_return
    rid = x.ready()
    if stage == "refund":
        x.post(rid)

    def revoke():
        raise BusinessError("SESSION_REVOKED", "提交前撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        (x.post if stage == "post" else x.refund)(rid)
    if stage == "post":
        assert x.stock() == 7
        assert x.detail(rid)["custody"][0]["outcome"] is None
        assert x.detail(rid)["document"]["state"] == "APPROVED"
        x.post(rid)
    assert x.stock() == 8
    assert x.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 0
    assert x.refund(rid)["amount"] == "20.00"


def test_retail_returns_and_cash_refunds_are_tenant_entity_role_scoped_and_immutable(retail_return):
    x = retail_return
    rid = x.ready()
    x.post(rid)
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert retail_refunds.detail(x.service, viewer, rid)["remaining_amount"] == "20.00"
    for ctx in [
        viewer,
        replace(x.f.maker, permissions=ROLE_PERMISSIONS["OPERATOR"]),
        replace(x.f.maker, entity_ids=frozenset()),
        replace(x.f.maker, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            x.refund(rid, ctx=ctx)
    x.refund(rid)
    for sql in [
        "UPDATE med.retail_return_refund SET amount=1",
        "DELETE FROM med.retail_return_refund",
    ]:
        with pytest.raises(DBAPIError):
            x.world.execute(sql)
    with pytest.raises(BusinessError):
        x.r.refund(x.sid)  # Unfulfilled-sale refund must remain blocked.


def test_uncompleted_retail_sale_is_not_a_return_source(retail_return):
    x = retail_return
    sid = x.r.create()["id"]
    x.r.pay(sid)
    rows = x.service.sources(x.f.maker, x.world.entity, "RETAIL", 0)["items"]
    assert len(rows) == 1 and rows[0]["retail_sale_id"] == x.sid
    with pytest.raises(BusinessError):
        x.service.source_detail(x.f.maker, UUID(sid))


def test_zero_price_return_restores_stock_without_creating_cash_refund(retail_return):
    x = retail_return
    sale = x.r.create(
        lines=[
            {"sku_id": x.f.sku, "qty": "1", "unit_price": p, "tax_rate": "0.13"}
            for p in ("0", "20")
        ]
    )
    x.r.pay(sale["id"])
    result = x.r.complete(sale["id"])
    pid = UUID(result["posting_id"])
    source = x.service.source_detail(x.f.maker, pid)
    line = next(r for r in source["lines"] if r["gross_amount"] == "0.00")
    rid = UUID(
        x.service.create(
            x.f.maker,
            ReturnCreate(
                client_operation_id=uuid4(),
                source_posting_id=pid,
                reason="退回零金额行",
                lines=[{"source_posting_line_id": line["id"], "qty": "1"}],
            ),
            str(uuid4()),
        )["id"]
    )
    x.receive(rid)
    x.review(rid)
    x.post(rid)
    assert x.detail(rid)["document"]["gross_amount"] == "0.00"
    with pytest.raises(BusinessError) as err:
        x.refund(rid)
    assert err.value.code == "NO_REFUND_DUE"


def test_two_different_refund_operations_cannot_refund_one_return_twice(retail_return):
    x = retail_return
    rid = x.ready()
    x.post(rid)
    bodies = [x.refund_body(rid), x.refund_body(rid)]

    def run(body):
        try:
            return x.refund(rid, body)["amount"]
        except BusinessError:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, bodies)) == ["20.00", "CONFLICT"]
    assert x.world.scalar("SELECT sum(amount) FROM med.retail_return_refund") == 20


def test_closed_period_prevents_cash_refund_but_keeps_accepted_return(retail_return):
    x = retail_return
    rid = x.ready()
    x.post(rid)
    # Closing periods is intentionally unavailable to the runtime role.
    owner = create_engine(os.environ["MED_MIGRATION_URL"], hide_parameters=True)
    try:
        with owner.begin() as conn:
            conn.execute(text("SET ROLE med_owner"))
            conn.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(x.f.maker.tenant_id)}
            )
            conn.execute(
                text("UPDATE med.accounting_period SET state='CLOSED' WHERE entity_id=:e"),
                {"e": x.world.entity},
            )
    finally:
        owner.dispose()
    with pytest.raises(BusinessError) as err:
        x.refund(rid)
    assert err.value.code == "PERIOD_CLOSED"
    assert x.stock() == 8
    assert x.detail(rid)["document"]["state"] == "POSTED"
    assert x.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 0
