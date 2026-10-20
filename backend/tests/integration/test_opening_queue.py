from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_openings import opened as opened
from test_procurement import flow as flow
from test_work_queue import kinds, queue

from medinv.modules.workspace.application.queue import WorkKind
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_opening_handoff_is_read_only_searchable_and_posts_once(opened):
    x, f = opened, opened.f
    oid = x.create()
    for kind, advance in [
        (WorkKind.OPENING_REVIEW, lambda: x.review(oid)),
        (WorkKind.OPENING_POST, lambda: x.post(oid)),
    ]:
        before = f.world.scalar("SELECT count(*) FROM med.audit_event")
        assert kinds(f, oid) == {kind.value}
        item = queue(f, kind)["items"][0]
        assert item["item_id"] == item["target_id"] == str(oid)
        assert item["page"] == "openings" and item["related_number"] == "OPENING-LIST"
        assert item["party_name"] == "期初库存" and not item["reservation_expired"]
        for search in (item["number"].lower(), item["warehouse_name"], "opening-list"):
            assert queue(f, kind, search=search)["items"] == [item]
        assert queue(f, kind, search="%_")["items"] == []
        assert queue(f, kind, search="' OR 1=1 --")["items"] == []
        assert f.world.scalar("SELECT count(*) FROM med.audit_event") == before
        assert (
            f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid)
            == 0
        )
        advance()
    assert kinds(f, oid) == set()
    x.post(oid)
    assert f.world.scalar(
        "SELECT sum(on_hand) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid
    ) == Decimal(10)
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_ledger WHERE event_type='OPENING_BALANCE'")
        == 1
    )
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 0


@pytest.mark.parametrize("terminal", ["draft_cancel", "approved_cancel", "reject"])
def test_closed_opening_leaves_queue_without_stock_and_allows_replacement(opened, terminal):
    x, f = opened, opened.f
    oid = x.create()
    if terminal == "reject":
        x.review(oid, decision="REJECT")
    else:
        if terminal == "approved_cancel":
            x.review(oid)
        x.service.transition(f.maker, oid, x.transition(oid), str(uuid4()), False)
    assert kinds(f, oid) == set()
    assert (
        f.world.scalar("SELECT count(*) FROM med.stock_balance WHERE warehouse_id=:w", w=x.wid) == 0
    )
    assert kinds(f, x.create()) == {"opening_review"}


def test_failed_independent_review_or_post_keeps_original_opening_work(opened):
    x = opened
    oid = x.create()
    with pytest.raises(BusinessError) as error:
        x.review(oid, ctx=x.f.maker)
    assert error.value.code == "MAKER_CHECKER"
    assert kinds(x.f, oid) == {"opening_review"}
    x.review(oid)
    with pytest.raises(BusinessError) as error:
        x.post(oid, ctx=x.f.maker)
    assert error.value.code == "MAKER_CHECKER"
    assert kinds(x.f, oid) == {"opening_post"}


def test_opening_queue_obeys_read_permission_and_tenant_entity_scope(opened):
    x, f = opened, opened.f
    oid = x.create()
    reader = replace(f.maker, permissions=frozenset({"inventory.read"}))
    data = queue(f, ctx=reader)
    assert {k["code"] for k in data["kinds"]} == {"opening_review", "opening_post"}
    assert [r["target_id"] for r in data["items"]] == [str(oid)]
    for ctx in [
        replace(reader, permissions=frozenset({"purchase.read"})),
        replace(reader, entity_ids=frozenset()),
        replace(reader, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            queue(f, WorkKind.OPENING_REVIEW, ctx=ctx)
    with pytest.raises(BusinessError):
        queue(f, WorkKind.OPENING_REVIEW, ctx=reader, entity=uuid4())
    with pytest.raises(BusinessError) as error:
        x.review(oid, ctx=reader)
    assert error.value.status == 403


def test_http_opening_work_changes_stage_and_is_not_cached(api, opened):
    client, owner = api
    x, f = opened, opened.f
    oid = x.create()
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['inventory.read']
            WHERE tenant_id=:t AND principal_id=:p"""),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    params = {"entity_id": str(f.world.entity), "kind": "opening_review"}
    response = client.get("/api/v1/work-queue", params=params)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["target_id"] == str(oid)
    x.review(oid)
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"] == []
    params["kind"] = "opening_post"
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"][0][
        "target_id"
    ] == str(oid)
