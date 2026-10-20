from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_stock_counts import counted as counted
from test_work_queue import kinds, queue

from medinv.modules.workspace.application.queue import WorkKind
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("actual,cost", [("8", "92.032"), ("12", "138.048"), ("10", "115.04")])
def test_count_work_stages_preserve_freeze_until_post_and_allow_fulfillment(counted, actual, cost):
    x, f = counted, counted.f
    cid = x.create()
    demand = x.w.demand("1", sku=f.sku)
    for kind, advance in [
        (WorkKind.COUNT_ENTER, lambda: x.submit(cid, actual)),
        (WorkKind.COUNT_REVIEW, lambda: x.review(cid)),
        (WorkKind.COUNT_POST, lambda: x.post(cid)),
    ]:
        before = x.w.scalar("SELECT count(*) FROM med.audit_event")
        assert kinds(f, cid) == {kind.value}
        item = queue(f, kind)["items"][0]
        assert item["item_id"] == item["target_id"] == str(cid)
        assert item["page"] == "counts" and item["related_number"] is None
        assert item["number"] == x.detail(cid)["document"]["number"]
        assert item["party_name"] == "库存盘点" and item["reservation_expired"] is False
        assert queue(f, kind, search=item["warehouse_name"])["items"] == [item]
        assert queue(f, search=item["number"].lower())["items"] == [item]
        assert queue(f, kind, search="%_")["items"] == []
        assert x.w.scalar("SELECT count(*) FROM med.audit_event") == before
        assert x.stock() == 10
        assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal("115.04")
        with pytest.raises(BusinessError) as error:
            x.w.service.reserve(x.w.ctx, demand, str(uuid4()))
        assert error.value.code == "COUNT_FROZEN"
        advance()
    assert kinds(f, cid) == set()
    assert x.stock() == Decimal(actual)
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal(cost)
    assert x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert x.w.service.reserve(x.w.ctx, demand, str(uuid4()))
    assert x.w.scalar("SELECT reserved FROM med.stock_balance WHERE id=:id", id=x.balance) == 1


@pytest.mark.parametrize("stage", ["COUNTING", "SUBMITTED", "APPROVED"])
def test_cancelled_count_work_disappears_and_recount_can_start(counted, stage):
    x = counted
    cid = x.create()
    if stage != "COUNTING":
        x.submit(cid)
    if stage == "APPROVED":
        x.review(cid)
    assert kinds(x.f, cid)
    x.service.cancel(x.f.maker, cid, x.transition(cid), str(uuid4()))
    assert kinds(x.f, cid) == set()
    assert x.stock() == 10
    replacement = x.create()
    assert kinds(x.f, replacement) == {"count_enter"}


def test_rejected_count_leaves_queue_without_recording_a_loss(counted):
    x = counted
    cid = x.create()
    x.submit(cid)
    x.review(cid, decision="REJECT")
    assert kinds(x.f, cid) == set()
    assert x.stock() == 10
    assert x.w.scalar("SELECT count(*) FROM med.posting_batch WHERE source_type='STOCK_COUNT'") == 0
    assert x.create()


def test_invalid_reading_and_self_review_do_not_advance_work(counted):
    x = counted
    cid = x.create()
    with pytest.raises(BusinessError) as error:
        x.submit(cid, overrides={"lot_no": "WRONG"})
    assert error.value.code == "COUNT_LOT_MISMATCH"
    assert kinds(x.f, cid) == {"count_enter"}
    x.submit(cid)
    with pytest.raises(BusinessError) as error:
        x.review(cid, ctx=x.f.maker)
    assert error.value.code == "MAKER_CHECKER"
    assert kinds(x.f, cid) == {"count_review"}


def test_failed_stale_post_keeps_work_and_freeze_until_cancel(counted):
    x = counted
    cid = x.ready()
    x.maintenance(cid, "UPDATE med.cost_balance SET version=version+1")
    with pytest.raises(BusinessError) as error:
        x.post(cid)
    assert error.value.code == "COUNT_STALE"
    assert kinds(x.f, cid) == {"count_post"}
    demand = x.w.demand("1", sku=x.f.sku)
    with pytest.raises(BusinessError) as error:
        x.w.service.reserve(x.w.ctx, demand, str(uuid4()))
    assert error.value.code == "COUNT_FROZEN"
    x.service.cancel(x.f.maker, cid, x.transition(cid), str(uuid4()))
    assert kinds(x.f, cid) == set()
    assert x.w.service.reserve(x.w.ctx, demand, str(uuid4()))


def test_count_read_permission_entity_and_tenant_fences(counted):
    x, f = counted, counted.f
    cid = x.create()
    reader = replace(f.maker, permissions=frozenset({"counts.read"}))
    data = queue(f, ctx=reader)
    assert {k["code"] for k in data["kinds"]} == {"count_enter", "count_review", "count_post"}
    assert data["items"][0]["target_id"] == str(cid)
    for context in [
        replace(reader, permissions=frozenset({"purchase.read"})),
        replace(reader, entity_ids=frozenset()),
        replace(reader, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            queue(f, WorkKind.COUNT_ENTER, ctx=context)
    with pytest.raises(BusinessError):
        queue(f, WorkKind.COUNT_ENTER, ctx=reader, entity=uuid4())
    with pytest.raises(BusinessError) as error:
        x.service.cancel(reader, cid, x.transition(cid), str(uuid4()))
    assert error.value.status == 403


def test_http_count_work_is_read_only_and_not_cached(api, counted):
    client, owner = api
    x, f = counted, counted.f
    cid = x.create()
    params = {"entity_id": str(x.w.entity), "kind": "count_enter"}
    assert client.get("/api/v1/work-queue", params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['counts.read']
            WHERE tenant_id=:t AND principal_id=:p"""),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    response = client.get("/api/v1/work-queue", params=params)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["target_id"] == str(cid)
    x.submit(cid)
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"] == []
    params["kind"] = "count_review"
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"][0][
        "target_id"
    ] == str(cid)
