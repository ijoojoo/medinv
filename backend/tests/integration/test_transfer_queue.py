from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_transfer_losses import losses as losses
from test_transfers import transferred as transferred
from test_work_queue import kinds, queue

from medinv.modules.workspace.application.queue import WorkKind
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_transfer_queue_advances_through_partial_and_quarantine_receipts(transferred):
    x, f = transferred, transferred.f
    tid = x.create()
    before = x.w.scalar("SELECT count(*) FROM med.audit_event")
    item = queue(f, WorkKind.TRANSFER_REVIEW)["items"][0]
    assert item["item_id"] == item["target_id"] == str(tid)
    assert item["page"] == "transfers" and item["related_number"] is None
    assert item["number"] == x.detail(tid)["document"]["number"]
    assert item["party_name"] == "内部调拨" and " → 调入仓" in item["warehouse_name"]
    assert item["reservation_expired"] is False
    assert queue(f, search=item["number"].lower())["items"] == [item]
    assert queue(f, WorkKind.TRANSFER_REVIEW, search="调入仓")["items"] == [item]
    assert queue(f, WorkKind.TRANSFER_REVIEW, search=item["warehouse_name"].split(" → ")[0])[
        "items"
    ] == [item]
    assert queue(f, WorkKind.TRANSFER_REVIEW, search="%_")["items"] == []
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == before
    assert x.stock(x.w.warehouse) == 10
    x.review(tid)
    assert kinds(f, tid) == {"transfer_dispatch"}
    x.dispatch(tid)
    assert kinds(f, tid) == {"transfer_receive"}
    x.receive(tid, "8")
    assert kinds(f, tid) == {"transfer_receive"}
    assert len(queue(f, WorkKind.TRANSFER_RECEIVE)["items"]) == 1
    x.receive(tid, "2", "QUARANTINE")
    assert kinds(f, tid) == set()
    assert x.stock(x.dest["id"]) == 10
    assert x.stock(x.detail(tid)["document"]["transit_warehouse_id"]) == 0


@pytest.mark.parametrize("terminal", ["cancel_draft", "cancel_approved", "reject"])
def test_unshipped_cancelled_transfer_disappears(transferred, terminal):
    x, f = transferred, transferred.f
    tid = x.create()
    if terminal == "reject":
        x.review(tid, decision="REJECT")
    else:
        if terminal == "cancel_approved":
            x.review(tid)
        x.service.transition(f.maker, tid, x.transition(tid), str(uuid4()))
    assert kinds(f, tid) == set()
    assert x.stock(x.w.warehouse) == 10


def test_failed_dispatch_keeps_work_until_stock_is_resolved(transferred):
    x, f = transferred, transferred.f
    tid = x.create()
    x.review(tid)
    x.w.execute("UPDATE med.stock_balance SET reserved=1 WHERE id=:b", b=x.source)
    with pytest.raises(BusinessError) as error:
        x.dispatch(tid)
    assert error.value.code == "INSUFFICIENT_STOCK"
    assert kinds(f, tid) == {"transfer_dispatch"}
    assert x.stock(x.w.warehouse) == 10


@pytest.mark.parametrize("quantity", ["2", "10"])
def test_pending_loss_does_not_close_work_until_loss_and_receipts_are_posted(losses, quantity):
    q, x = losses, losses.x
    if quantity == "2":
        x.receive(q.tid, "8")
    lid = q.create(quantity)
    assert kinds(x.f, q.tid) == {"transfer_receive", "transfer_loss_review"}
    q.review(lid)
    assert kinds(x.f, q.tid) == {"transfer_receive", "transfer_loss_post"}
    q.post(lid)
    assert x.detail(q.tid)["document"]["state"] == "CLOSED_WITH_LOSS"
    assert kinds(x.f, q.tid) == set()


def test_partial_posted_loss_keeps_remaining_physical_receipt_visible(losses):
    q, x = losses, losses.x
    q.post(q.ready("2"))
    assert kinds(x.f, q.tid) == {"transfer_receive"}
    assert x.detail(q.tid)["lines"][0]["remaining_qty"] == "8.000000"
    x.receive(q.tid, "8")
    assert kinds(x.f, q.tid) == set()


def test_transfer_read_permission_entity_and_tenant_isolation(transferred):
    x, f = transferred, transferred.f
    tid = x.create()
    reader = replace(f.maker, permissions=frozenset({"transfers.read"}))
    data = queue(f, ctx=reader)
    assert {k["code"] for k in data["kinds"]} == {
        "transfer_review",
        "transfer_dispatch",
        "transfer_receive",
        "transfer_loss_review",
        "transfer_loss_post",
    }
    assert data["items"][0]["target_id"] == str(tid)
    for context in [
        replace(reader, permissions=frozenset({"purchase.read"})),
        replace(reader, entity_ids=frozenset()),
        replace(reader, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            queue(f, WorkKind.TRANSFER_REVIEW, ctx=context)
    with pytest.raises(BusinessError):
        queue(f, WorkKind.TRANSFER_REVIEW, ctx=reader, entity=uuid4())
    with pytest.raises(BusinessError) as error:
        x.service.transition(reader, tid, x.transition(tid), str(uuid4()))
    assert error.value.status == 403


def test_http_transfer_work_queue_uses_original_document_and_no_store(api, transferred):
    client, owner = api
    x, f = transferred, transferred.f
    tid = x.create()
    params = {"entity_id": str(x.w.entity), "kind": "transfer_review"}
    assert client.get("/api/v1/work-queue", params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['transfers.read']
        WHERE tenant_id=:t AND principal_id=:p"""),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    response = client.get("/api/v1/work-queue", params=params)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["target_id"] == str(tid)
    x.review(tid)
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"] == []
    params["kind"] = "transfer_dispatch"
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"][0][
        "target_id"
    ] == str(tid)
