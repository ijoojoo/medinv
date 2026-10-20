from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_sales import sale as sale
from test_settlement import finance as finance

from medinv.modules.procurement.api.schemas import InspectionCreate, ReceiptCreate, Transition
from medinv.modules.returns.api.schemas import (
    RefundCreate,
    ReturnCreate,
    ReturnReceive,
    ReturnReview,
)
from medinv.modules.returns.application import custody, posting, refunds
from medinv.modules.returns.application.service import ReturnService
from medinv.modules.settlement.api.schemas import ReverseRequest
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def ret(finance):
    f = finance
    service = ReturnService(f.s.f.world.uow)

    def create(kind="SALES", op=None, lines=None):
        source = f.items("AR" if kind == "SALES" else "AP")[0]
        return UUID(
            service.create(
                f.maker,
                ReturnCreate(
                    client_operation_id=op or uuid4(),
                    source_posting_id=source["posting_id"],
                    reason="整批退货，实物单独保管",
                    lines=lines,
                ),
                str(uuid4()),
            )["id"]
        )

    def detail(rid):
        return service.detail(f.maker, rid)

    def review_body(rid):
        d = detail(rid)
        return ReturnReview(
            expected_version=d["document"]["version"],
            reason="逐件核对质量、数量和批号",
            decision="APPROVE",
            lines=[
                {"source_posting_line_id": r["id"], "qty": r["base_qty"], "lot_no": r["lot_no"]}
                for r in d["lines"]
            ],
        )

    def receive(rid):
        d = detail(rid)
        return custody.receive(
            service,
            f.maker,
            rid,
            ReturnReceive(
                expected_version=d["document"]["version"],
                reason="实物交接清点",
                zone_label="测试仓退货隔离区 A-01",
                confirmed=True,
                lines=review_body(rid).lines,
            ),
            str(uuid4()),
        )

    def review(rid):
        d = detail(rid)["document"]
        if d["kind"] == "SALES" and d["state"] == "DRAFT":
            receive(rid)
        return service.review(f.s.f.reviewer, rid, review_body(rid), str(uuid4()))

    def post(rid):
        return posting.post(
            service,
            f.s.f.reviewer,
            rid,
            Transition(expected_version=detail(rid)["document"]["version"], reason="确认实物交接"),
            str(uuid4()),
        )

    def ready(kind="SALES"):
        rid = create(kind)
        review(rid)
        return rid

    def refund_body(rid, cid, amount="60", op=None, ref=None):
        c = f.detail(cid)["cash"]
        return RefundCreate(
            client_operation_id=op or uuid4(),
            cash_document_id=cid,
            return_document_id=rid,
            expected_cash_version=c["version"],
            entity_id=c["entity_id"],
            party_id=c["party_id"],
            account_id=c["account_id"],
            direction="PAYMENT" if c["direction"] == "RECEIPT" else "RECEIPT",
            amount=amount,
            reference=ref or str(uuid4()),
            occurred_on=f.s.f.day,
            note="核对实际退款凭据",
            confirmed=True,
        )

    def refund(rid, cid, amount="60", body=None):
        return refunds.create(service, f.maker, body or refund_body(rid, cid, amount), str(uuid4()))

    return SimpleNamespace(**locals())


def test_sales_return_restores_original_cost_and_credit_without_reopening_order(ret):
    r = ret
    rid = r.ready()
    first = r.post(rid)
    assert r.post(rid) == first
    assert r.f.s.scalar("on_hand") == 10
    assert r.f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("115.04")
    assert r.f.items()[0]["gross_amount"] == "60.00"
    assert r.f.items()[0]["returned_amount"] == "60.00"
    assert r.f.items()[0]["outstanding_amount"] == "0.00"
    credit = r.f.s.service.credit(r.f.maker, r.f.s.f.world.entity, r.f.s.customer)
    assert credit["receivable"] == "0.00" and credit["exposure"] == "140.00"
    assert r.f.s.detail()["lines"][0]["fulfilled_qty"] == "3.000000"


def test_purchase_return_requires_available_stock_and_returns_ap_without_touching_ar(ret):
    r = ret
    purchase = r.ready("PURCHASE")
    with pytest.raises(BusinessError) as err:
        r.post(purchase)
    assert err.value.code == "RETURN_STOCK_SHORT"
    assert r.detail(purchase)["document"]["state"] == "APPROVED"
    r.post(r.ready())
    result = r.post(purchase)
    assert result["purchase_cost_variance"] == "0.00000000"
    assert r.f.s.scalar("on_hand") == 0
    assert r.f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == 0
    assert r.f.items("AP")[0]["returned_amount"] == "130.00"
    assert r.f.items("AP")[0]["outstanding_amount"] == "0.00"


def test_paid_return_requires_reversal_then_partial_refunds_preserve_original_cash(ret):
    r = ret
    cid = r.f.cash("80")["id"]
    allocation = r.f.allocate(cid)["allocation_ids"][0]
    rid = r.ready()
    with pytest.raises(BusinessError) as err:
        r.post(rid)
    assert err.value.code == "RETURN_REVERSE_ALLOCATIONS"
    r.f.reverse(allocation)
    r.post(rid)
    r.refund(rid, cid, "20")
    r.refund(rid, cid, "40")
    cash = r.f.detail(cid)["cash"]
    assert (
        cash["amount"] == "80.00"
        and cash["refunded_amount"] == "60.00"
        and cash["remaining_amount"] == "20.00"
    )
    assert refunds.listing(r.service, r.f.maker, rid, 0)["remaining_amount"] == "0.00"
    with pytest.raises(BusinessError) as err:
        r.refund(rid, cid, "1")
    assert err.value.code == "REFUND_EXCEEDED"
    assert r.f.items()[0]["outstanding_amount"] == "0.00"


def paid_return(r):
    cid = r.f.cash()["id"]
    aid = r.f.allocate(cid)["allocation_ids"][0]
    r.f.reverse(aid)
    rid = r.ready()
    r.post(rid)
    return rid, cid


def test_refund_concurrency_and_permanent_replay_and_void(ret):
    r = ret
    rid, cid = paid_return(r)
    body = r.refund_body(rid, cid)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: r.refund(rid, cid, body=body), range(6)))
    assert all(x == results[0] for x in results)
    assert r.f.s.f.world.scalar("SELECT count(*) FROM med.cash_refund") == 1
    req = ReverseRequest(client_operation_id=uuid4(), reason="退款登记填错，实际资金未退")
    for _ in range(2):
        refunds.void(r.service, r.f.maker, UUID(results[0]["id"]), req, str(uuid4()))
    assert r.f.detail(cid)["cash"]["remaining_amount"] == "60.00"
    assert refunds.listing(r.service, r.f.maker, rid, 0)["items"][0]["void_reason"] == req.reason
    r.refund(rid, cid)
    assert r.f.detail(cid)["cash"]["remaining_amount"] == "0.00"


def test_refunded_cash_cannot_be_allocated_or_voided_again(ret):
    r = ret
    rid, cid = paid_return(r)
    r.refund(rid, cid)
    r.f.s.post(r.f.s.ready("1"))
    with pytest.raises(BusinessError) as err:
        r.f.allocate(cid, "20")
    assert err.value.code == "CASH_EXCEEDED"
    with pytest.raises(BusinessError):
        r.f.service.void(
            r.f.maker,
            UUID(cid),
            Transition(
                expected_version=r.f.detail(cid)["cash"]["version"], reason="不能作废已退款款项"
            ),
            str(uuid4()),
        )


def test_refund_requires_original_allocation_and_posted_return(ret):
    r = ret
    rid = r.ready()
    cid = r.f.cash()["id"]
    with pytest.raises(BusinessError) as err:
        r.refund(rid, cid)
    assert err.value.code == "REFUND_STATE"
    r.post(rid)
    with pytest.raises(BusinessError) as err:
        r.refund(rid, cid)
    assert err.value.code == "REFUND_SOURCE"


def test_return_review_requires_independent_actor_and_exact_manifest(ret):
    r = ret
    rid = r.create()
    with pytest.raises(BusinessError) as err:
        r.service.review(r.f.maker, rid, r.review_body(rid), str(uuid4()))
    assert err.value.code == "MAKER_CHECKER"
    r.receive(rid)
    body = r.review_body(rid)
    body.lines[0].qty = "2.000000"
    with pytest.raises(BusinessError) as err:
        r.service.review(r.f.s.f.reviewer, rid, body, str(uuid4()))
    assert err.value.code == "RETURN_CHECK_MISMATCH"
    r.review(rid)
    with pytest.raises(BusinessError):
        posting.post(
            r.service,
            r.f.maker,
            rid,
            Transition(expected_version="2", reason="越权过账"),
            str(uuid4()),
        )


def test_concurrent_create_and_post_never_return_same_source_twice(ret):
    r = ret
    op = uuid4()
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: r.create(op=op), range(6)))
    assert len(set(ids)) == 1
    r.review(ids[0])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: r.post(ids[0]), range(6)))
    assert all(x == results[0] for x in results)
    with pytest.raises(BusinessError) as err:
        r.create()
    assert err.value.code == "RETURN_EXISTS"
    assert r.f.s.scalar("on_hand") == 10


def test_cancel_can_recreate_but_posted_return_cannot_cancel(ret):
    r = ret
    rid = r.create()
    r.service.cancel(
        r.f.maker, rid, Transition(expected_version="1", reason="实物未退，取消申请"), str(uuid4())
    )
    rid2 = r.ready()
    r.post(rid2)
    with pytest.raises(BusinessError):
        r.service.cancel(
            r.f.maker, rid2, Transition(expected_version="3", reason="不可删除历史"), str(uuid4())
        )


def test_return_rechecks_lot_after_approval_and_rolls_back(ret):
    r = ret
    rid = r.ready()
    r.f.s.f.world.execute("UPDATE med.lot SET quality_blocked=true")
    with pytest.raises(BusinessError) as err:
        r.post(rid)
    assert err.value.code == "RETURN_LOT_INELIGIBLE"
    assert r.f.s.scalar("on_hand") == 7
    assert r.f.items()[0]["outstanding_amount"] == "60.00"


def test_authorization_probe_failure_rolls_back_return_and_refund(ret):
    r = ret
    rid = r.ready()

    def fail():
        raise BusinessError("SESSION_EXPIRED", "会话失效", 401)

    with before_business_commit.set(fail), pytest.raises(BusinessError):
        r.post(rid)
    assert r.f.s.scalar("on_hand") == 7
    cid = r.f.cash()["id"]
    aid = r.f.allocate(cid)["allocation_ids"][0]
    r.f.reverse(aid)
    r.post(rid)
    with before_business_commit.set(fail), pytest.raises(BusinessError):
        r.refund(rid, cid)
    assert r.f.detail(cid)["cash"]["refunded_amount"] == "0.00"


def test_return_tenant_entity_permissions_and_immutable_refund_facts(ret):
    r = ret
    rid, cid = paid_return(r)
    f = r.refund(rid, cid)
    for ctx in (replace(r.f.maker, tenant_id=uuid4()), replace(r.f.maker, entity_ids=frozenset())):
        with pytest.raises(BusinessError):
            r.service.detail(ctx, rid)
    viewer = replace(r.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    with pytest.raises(BusinessError):
        refunds.create(r.service, viewer, r.refund_body(rid, cid, "1"), str(uuid4()))
    with pytest.raises(DBAPIError):
        r.f.s.f.world.execute("UPDATE med.cash_refund SET amount=1 WHERE id=:id", id=UUID(f["id"]))
    with pytest.raises(DBAPIError):
        r.f.s.f.world.execute("UPDATE med.return_document SET gross_amount=1 WHERE id=:id", id=rid)


def test_supplier_refund_is_incoming_and_does_not_change_customer_credit(ret):
    r = ret
    r.post(r.ready())
    cid = r.f.cash("130", "PAYMENT")["id"]
    aid = r.f.allocate(cid, "130", "AP")["allocation_ids"][0]
    r.f.reverse(aid)
    rid = r.ready("PURCHASE")
    r.post(rid)
    r.refund(rid, cid, "130")
    refund = refunds.listing(r.service, r.f.maker, rid, 0)["items"][0]
    assert refund["direction"] == "RECEIPT"
    assert (
        r.f.s.service.credit(r.f.maker, r.f.s.f.world.entity, r.f.s.customer)["receivable"]
        == "0.00"
    )
    assert r.f.detail(cid)["cash"]["remaining_amount"] == "0.00"


def test_refund_reference_cannot_be_reused_by_normal_cash_registration(ret):
    r = ret
    rid, cid = paid_return(r)
    body = r.refund_body(rid, cid, "20", ref="BANK-REFUND-UNIQUE")
    r.refund(rid, cid, body=body)
    with pytest.raises(BusinessError) as err:
        r.f.cash("20", "PAYMENT", ref=body.reference)
    assert err.value.code == "CASH_REFERENCE_EXISTS"
    with pytest.raises(BusinessError) as err:
        r.refund(rid, cid, body=r.refund_body(rid, cid, "20", ref=body.reference))
    assert err.value.code == "CASH_REFERENCE_EXISTS"


def test_purchase_return_weighted_cost_and_variance_after_same_lot_replenishment(ret):
    r = ret
    r.post(r.ready())
    rid = r.ready("PURCHASE")
    f = r.f.s.f
    oid = f.order(qty="4", price="26")
    detail = f.service.detail(f.maker, oid)
    rec = f.service.receive(
        f.maker,
        oid,
        ReceiptCreate(
            expected_version=detail["order"]["version"],
            shipment_ref="SECOND",
            transport_note="同批次补货",
            lines=[
                {
                    "order_line_id": detail["lines"][0]["id"],
                    "location_id": f.world.location,
                    "qty": "4",
                    "lot_no": "B001",
                    "mfg_date": f.day - timedelta(days=2),
                    "expiry_date": f.day + timedelta(days=365),
                }
            ],
        ),
        str(uuid4()),
    )
    detail = f.service.detail(f.maker, oid)["receipts"][0]
    inspection = f.service.inspect(
        f.reviewer,
        UUID(rec["id"]),
        InspectionCreate(
            expected_version=detail["version"],
            note="同批次验收",
            lines=[
                {
                    "receipt_line_id": detail["lines"][0]["id"],
                    "qualified_qty": "4",
                    "rejected_qty": "0",
                }
            ],
        ),
        str(uuid4()),
    )
    f.post(inspection)
    before = f.world.scalar("SELECT sum(amount) FROM med.cost_balance")
    expected = (before * Decimal(10) / Decimal(14)).quantize(
        Decimal(".00000001"), rounding=ROUND_HALF_UP
    )
    result = r.post(rid)
    assert Decimal(result["cost_amount"]) == expected
    assert Decimal(result["purchase_cost_variance"]) == Decimal("115.04") - expected
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == before - expected
    assert r.f.s.scalar("on_hand") == 4


def test_refund_uses_latest_cash_version_and_rejects_future_date(ret):
    r = ret
    rid, cid = paid_return(r)
    stale = r.refund_body(rid, cid, "20")
    r.refund(rid, cid, "20")
    with pytest.raises(BusinessError):
        r.refund(rid, cid, body=stale)
    body = r.refund_body(rid, cid, "20")
    body.occurred_on = r.f.s.f.day + timedelta(days=1)
    with pytest.raises(BusinessError) as err:
        r.refund(rid, cid, body=body)
    assert err.value.code == "FUTURE_CASH_DATE"
    assert r.f.detail(cid)["cash"]["refunded_amount"] == "20.00"
