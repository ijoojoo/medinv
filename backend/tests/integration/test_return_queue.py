from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow
from test_retail import retail as retail
from test_retail_returns import retail_return as retail_return
from test_returns import ret as ret
from test_sales import sale as sale
from test_settlement import finance as finance
from test_work_queue import kinds, queue

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.returns.api.schemas import CustodyHandover, ReturnCreate, ReturnReview
from medinv.modules.returns.application import custody, refunds
from medinv.modules.settlement.api.schemas import ReverseRequest
from medinv.modules.workspace.application.queue import WorkKind
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_wholesale_return_moves_from_receive_to_review_to_post_without_false_cash_task(ret):
    r, f = ret, ret.f.s.f
    rid = r.create()
    assert kinds(f, rid) == {"return_receive"}
    before = f.world.scalar("SELECT count(*) FROM med.audit_event")
    item = queue(f, WorkKind.RETURN_RECEIVE)["items"][0]
    assert item["page"] == "returns" and item["item_id"] == str(rid)
    assert item["number"] == r.detail(rid)["document"]["number"]
    assert item["related_number"] == r.detail(rid)["document"]["source_number"]
    assert item["warehouse_name"] and not item["reservation_expired"]
    assert queue(f, WorkKind.RETURN_RECEIVE, search=item["related_number"])["items"] == [item]
    assert f.world.scalar("SELECT count(*) FROM med.audit_event") == before
    r.receive(rid)
    assert kinds(f, rid) == {"return_review"}
    r.review(rid)
    assert kinds(f, rid) == {"return_post"}
    r.post(rid)
    assert kinds(f, rid) == set()  # Unpaid credit sale: return amount is not cash due.


def test_purchase_return_review_post_and_cancel(ret):
    r, f = ret, ret.f.s.f
    source = r.service.source_detail(f.maker, UUID(r.f.items("AP")[0]["posting_id"]))
    rid = r.create(
        "PURCHASE", lines=[{"source_posting_line_id": source["lines"][0]["id"], "qty": "5"}]
    )
    assert kinds(f, rid) == {"purchase_return_review"}
    r.review(rid)
    assert kinds(f, rid) == {"purchase_return_post"}
    r.post(rid)
    assert kinds(f, rid) == set()
    cancelled = r.create("PURCHASE")
    r.service.cancel(
        f.maker, cancelled, Transition(expected_version="1", reason="未退取消"), str(uuid4())
    )
    assert kinds(f, cancelled) == set()


@pytest.mark.parametrize("channel", ["SALES", "RETAIL"])
def test_rejected_physical_return_stays_pending_until_actual_handover(request, channel):
    x = request.getfixturevalue("ret" if channel == "SALES" else "retail_return")
    f = x.f.s.f if channel == "SALES" else x.f
    rid = x.create()
    x.receive(rid)
    x.service.review(
        f.reviewer,
        rid,
        ReturnReview(expected_version="2", decision="REJECT", reason="破损拒收", lines=[]),
        str(uuid4()),
    )
    assert kinds(f, rid) == {"return_handback"}
    custody.hand_back(
        x.service,
        f.maker,
        rid,
        CustodyHandover(expected_version="3", reason="实物已交还", confirmed=True),
        str(uuid4()),
    )
    assert kinds(f, rid) == set()


def test_retail_partial_return_refund_can_coexist_with_next_physical_return(retail_return):
    x, f = retail_return, retail_return.f
    first = x.create()
    assert kinds(f, first) == {"return_receive"}
    x.receive(first)
    assert kinds(f, first) == {"return_review"}
    x.review(first)
    assert kinds(f, first) == {"return_post"}
    x.post(first)
    second = x.create()
    assert kinds(f, first) == {"retail_return_refund"}
    assert kinds(f, second) == {"return_receive"}
    x.refund(first)
    assert kinds(f, first) == set()
    assert kinds(f, second) == {"return_receive"}


def test_zero_price_retail_return_does_not_require_cash_refund(retail_return):
    x, f = retail_return, retail_return.f
    sid = x.r.create(
        lines=[
            {"sku_id": f.sku, "qty": "1", "unit_price": price, "tax_rate": "0.13"}
            for price in ("0", "20")
        ]
    )["id"]
    x.r.pay(sid)
    pid = UUID(x.r.complete(sid)["posting_id"])
    line = next(
        r for r in x.service.source_detail(f.maker, pid)["lines"] if r["gross_amount"] == "0.00"
    )
    rid = UUID(
        x.service.create(
            f.maker,
            ReturnCreate(
                client_operation_id=uuid4(),
                source_posting_id=pid,
                reason="赠品退回",
                lines=[{"source_posting_line_id": line["id"], "qty": "1"}],
            ),
            str(uuid4()),
        )["id"]
    )
    x.receive(rid)
    x.review(rid)
    x.post(rid)
    assert kinds(f, rid) == set()


@pytest.mark.parametrize("channel,amount,qty", [("SALES", "60", "3"), ("PURCHASE", "65", "5")])
def test_cash_review_requires_original_available_payment_and_clears_or_reappears_on_void(
    ret, channel, amount, qty
):
    r, f = ret, ret.f.s.f
    kind = "AR" if channel == "SALES" else "AP"
    source = r.service.source_detail(f.maker, UUID(r.f.items(kind)[0]["posting_id"]))
    cid = r.f.cash(amount, "RECEIPT" if channel == "SALES" else "PAYMENT")["id"]
    aid = r.f.allocate(cid, amount, kind)["allocation_ids"][0]
    r.f.reverse(aid)
    rid = r.create(
        channel, lines=[{"source_posting_line_id": source["lines"][0]["id"], "qty": qty}]
    )
    r.review(rid)
    assert "return_refund_check" not in kinds(f, rid)
    r.post(rid)
    assert kinds(f, rid) == {"return_refund_check"}
    first = r.refund(rid, cid, "20")
    assert kinds(f, rid) == {"return_refund_check"}
    r.refund(rid, cid, str(int(amount) - 20))
    assert kinds(f, rid) == set()
    refunds.void(
        r.service,
        f.maker,
        UUID(first["id"]),
        ReverseRequest(
            client_operation_id=uuid4(),
            reason="实际退款登记有误，撤销后重新核对",
        ),
        str(uuid4()),
    )
    assert kinds(f, rid) == {"return_refund_check"}


def test_unrelated_or_exhausted_cash_does_not_create_false_refund_task(ret):
    r, f = ret, ret.f.s.f
    r.f.cash("60")  # Same party, but never allocated to this source.
    cid = r.f.cash("20")["id"]
    r.f.reverse(r.f.allocate(cid, "20")["allocation_ids"][0])
    rid = r.ready()
    r.post(rid)
    assert kinds(f, rid) == {"return_refund_check"}
    r.refund(rid, cid, "20")
    assert kinds(f, rid) == set()  # Remaining return cap 40 is not a cash debt.


def test_return_read_and_finance_read_are_both_required_for_cash_review(ret):
    f = ret.f.s.f
    readonly = replace(f.maker, permissions=frozenset({"returns.read"}))
    data = queue(f, ctx=readonly)
    assert len(data["kinds"]) == 7
    assert "return_refund_check" not in {k["code"] for k in data["kinds"]}
    both = replace(readonly, permissions=readonly.permissions | {"finance.read"})
    assert len(queue(f, ctx=both)["kinds"]) == 8
    for ctx in [readonly, replace(f.maker, permissions=frozenset({"finance.read"}))]:
        with pytest.raises(BusinessError) as error:
            queue(f, WorkKind.RETURN_REFUND_CHECK, ctx=ctx)
        assert error.value.status == 403
    rid = ret.create()
    assert queue(f, WorkKind.RETURN_RECEIVE, ctx=readonly)["items"][0]["target_id"] == str(rid)
    for ctx in [replace(readonly, entity_ids=frozenset()), replace(readonly, tenant_id=uuid4())]:
        with pytest.raises(BusinessError):
            queue(f, WorkKind.RETURN_RECEIVE, ctx=ctx)


def test_legacy_return_draft_goes_directly_to_review(ret):
    r, f = ret, ret.f.s.f
    rid = uuid4()
    f.world.execute(
        """INSERT INTO med.return_document(tenant_id,id,entity_id,party_id,
        source_posting_id,receivable_id,kind,number,reason,gross_amount,net_amount,created_by)
        SELECT tenant_id,:id,entity_id,customer_id,posting_id,id,'SALES',:number,'旧版申请',
        gross_amount,net_amount,:actor FROM med.receivable_obligation WHERE id=:source""",
        id=rid,
        number=str(rid),
        actor=f.maker.principal_id,
        source=UUID(r.f.items()[0]["id"]),
    )
    assert kinds(f, rid) == {"return_review"}
    r.service.review(f.reviewer, rid, r.review_body(rid), str(uuid4()))
    assert kinds(f, rid) == {"return_post"}
    r.post(rid)
    assert kinds(f, rid) == set()


def test_fully_allocated_source_cash_is_not_available_until_reversal(ret):
    r, f = ret, ret.f.s.f
    cid = r.f.cash("40")["id"]
    aid = r.f.allocate(cid, "40")["allocation_ids"][0]
    source = r.service.source_detail(f.maker, UUID(r.f.items()[0]["posting_id"]))
    rid = r.create(lines=[{"source_posting_line_id": source["lines"][0]["id"], "qty": "1"}])
    r.review(rid)
    r.post(rid)
    assert kinds(f, rid) == set()
    r.f.reverse(aid)
    assert kinds(f, rid) == {"return_refund_check"}
