from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow
from test_returns import ret as ret
from test_sales import sale as sale
from test_settlement import finance as finance

from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def source(r, kind="SALES", offset=0, ctx=None):
    pid = UUID(r.f.items("AR" if kind == "SALES" else "AP")[0]["posting_id"])
    return r.service.source_detail(ctx or r.f.maker, pid, offset)


def create_one(r, kind="SALES"):
    line = source(r, kind)["lines"][0]
    return r.create(kind, lines=[{"source_posting_line_id": line["id"], "qty": "1"}])


def cancel(r, rid):
    r.service.cancel(
        r.f.maker, rid, Transition(expected_version="1", reason="取消未交接申请"), str(uuid4())
    )


@pytest.mark.parametrize("kind,original,price", [("SALES", 3, 20), ("PURCHASE", 10, 13)])
def test_source_progress_keeps_cancelled_pending_and_posted_returns_distinct(
    ret, kind, original, price
):
    r = ret
    first = create_one(r, kind)
    data = source(r, kind)
    assert data["active_return"]["id"] == str(first)
    assert Decimal(data["source"]["returned_amount"]) == 0
    assert Decimal(data["lines"][0]["remaining_qty"]) == original
    cancel(r, first)
    assert source(r, kind)["active_return"] is None

    posted = create_one(r, kind)
    r.review(posted)
    r.post(posted)
    pending = create_one(r, kind)
    other = r.create("PURCHASE" if kind == "SALES" else "SALES")
    viewer = replace(r.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    data = source(r, kind, ctx=viewer)
    assert data["active_return"]["id"] == str(pending)
    assert Decimal(data["source"]["returned_amount"]) == price
    assert Decimal(data["lines"][0]["returned_qty"]) == 1
    assert Decimal(data["lines"][0]["remaining_qty"]) == original - 1
    states = {row["id"]: row["state"] for row in data["history"]["items"]}
    assert states == {str(first): "CANCELLED", str(posted): "POSTED", str(pending): "DRAFT"}
    assert str(other) not in states
    # Totals and the actionable pending document do not depend on the history page.
    later = source(r, kind, offset=50)
    assert later["history"] == {"items": [], "has_more": False}
    assert later["active_return"] == data["active_return"]
    assert later["source"]["returned_amount"] == data["source"]["returned_amount"]


def test_source_history_pages_without_duplicates_and_total_is_not_page_subtotal(ret):
    r = ret
    posted = create_one(r)
    r.review(posted)
    r.post(posted)
    for _ in range(50):
        cancel(r, create_one(r))
    pending = create_one(r)
    first, second = source(r), source(r, offset=50)
    assert len(first["history"]["items"]) == 50
    assert len(second["history"]["items"]) == 2
    assert first["history"]["has_more"] is True
    assert second["history"]["has_more"] is False
    ids = [row["id"] for d in (first, second) for row in d["history"]["items"]]
    assert len(set(ids)) == 52
    for data in (first, second):
        assert data["source"]["returned_amount"] == "20.00"
        assert data["active_return"]["id"] == str(pending)


@pytest.mark.parametrize("scope", ["tenant", "entity", "permission"])
def test_source_progress_is_not_visible_outside_authorized_scope(ret, scope):
    r = ret
    create_one(r)
    changes = {
        "tenant": {"tenant_id": uuid4()},
        "entity": {"entity_ids": frozenset()},
        "permission": {"permissions": frozenset()},
    }
    with pytest.raises(BusinessError):
        source(r, ctx=replace(r.f.maker, **changes[scope]))


def test_unknown_source_has_no_history(ret):
    with pytest.raises(BusinessError):
        ret.service.source_detail(ret.f.maker, uuid4())


def test_split_shipments_of_same_order_keep_separate_return_progress(ret):
    r = ret
    original = source(r)["source"]["posting_id"]
    r.f.s.post(r.f.s.ready("2"))
    current = source(r)["source"]["posting_id"]
    assert current != original
    pending = create_one(r)
    assert source(r)["active_return"]["id"] == str(pending)
    untouched = r.service.source_detail(r.f.maker, UUID(original))
    assert untouched["active_return"] is None
    assert untouched["history"] == {"items": [], "has_more": False}
    assert Decimal(untouched["source"]["returned_amount"]) == 0
    assert Decimal(untouched["lines"][0]["remaining_qty"]) == 3
