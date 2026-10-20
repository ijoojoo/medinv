from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_quality import isolated as isolated
from test_stock_counts import counted as counted
from test_transfer_losses import losses as losses
from test_transfers import transferred as transferred
from test_work_queue import kinds, queue

from medinv.modules.workspace.application.queue import WorkKind
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def job(ctx, role):
    return replace(ctx, permissions=ROLE_PERMISSIONS[role])


@pytest.mark.parametrize("kind,remaining", [("RELEASE", "10"), ("WRITE_OFF", "6")])
def test_quality_job_handoff_read_only_work_and_independent_posting(isolated, kind, remaining):
    q, f = isolated, isolated.x.f
    warehouse, quality = job(f.maker, "WAREHOUSE"), job(f.reviewer, "QUALITY")
    did = q.create(kind, "4", ctx=warehouse)
    before = q.x.w.scalar("SELECT count(*) FROM med.audit_event")
    item = queue(f, WorkKind.QUALITY_REVIEW, ctx=quality)["items"][0]
    assert item["target_id"] == item["item_id"] == str(did)
    assert item["page"] == "quality" and item["related_number"] is None
    assert item["party_name"] == ("隔离品放行" if kind == "RELEASE" else "隔离品报损")
    assert queue(f, WorkKind.QUALITY_REVIEW, search=item["warehouse_name"])["items"] == [item]
    assert queue(f, search=item["number"].lower())["items"] == [item]
    assert q.x.w.scalar("SELECT count(*) FROM med.audit_event") == before
    with pytest.raises(BusinessError) as error:
        q.review(did, ctx=warehouse)
    assert error.value.status == 403
    combined = replace(warehouse, permissions=warehouse.permissions | quality.permissions)
    with pytest.raises(BusinessError) as error:
        q.review(did, ctx=combined)
    assert error.value.code == "MAKER_CHECKER"
    q.review(did, ctx=quality)
    assert kinds(f, did) == {"quality_post"}
    assert q.stock("QUARANTINE") == 10
    with pytest.raises(BusinessError) as error:
        q.post(did, ctx=warehouse)
    assert error.value.status == 403
    q.post(did, ctx=quality)
    assert kinds(f, did) == set()
    assert q.stock("QUARANTINE") == 6
    assert q.stock("QUALIFIED") == (4 if kind == "RELEASE" else 0)
    assert q.x.w.scalar("SELECT qty FROM med.cost_balance") == Decimal(remaining)
    assert q.x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert q.x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0


@pytest.mark.parametrize("approved", [False, True])
def test_late_arrival_keeps_original_loss_visible_after_transfer_completion(losses, approved):
    q, x = losses, losses.x
    warehouse, quality = job(x.f.maker, "WAREHOUSE"), job(x.f.reviewer, "QUALITY")
    lid = q.create(ctx=warehouse)
    if approved:
        q.review(lid, ctx=quality)
    kind = WorkKind.TRANSFER_LOSS_POST if approved else WorkKind.TRANSFER_LOSS_REVIEW
    before = x.w.scalar("SELECT count(*) FROM med.audit_event")
    item = queue(x.f, kind, ctx=quality)["items"][0]
    assert item["target_id"] == str(q.tid) and item["item_id"] == str(lid)
    assert item["page"] == "transfers" and item["related_number"] == q.detail(lid)["number"]
    assert queue(x.f, kind, search=item["related_number"].lower())["items"] == [item]
    assert queue(x.f, kind, search=item["warehouse_name"].split(" → ")[1])["items"] == [item]
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == before
    x.receive(q.tid, "10")
    assert x.detail(q.tid)["document"]["state"] == "RECEIVED"
    assert kinds(x.f, q.tid) == {kind.value}
    if approved:
        with pytest.raises(BusinessError):
            q.post(lid, ctx=quality)
        assert kinds(x.f, q.tid) == {kind.value}
    q.service.transition(warehouse, q.tid, lid, q.transition(lid), str(uuid4()))
    assert kinds(x.f, q.tid) == set()
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal("115.04")


def test_warehouse_loss_hands_off_to_quality_without_extra_permissions(losses):
    q, x = losses, losses.x
    warehouse, quality = job(x.f.maker, "WAREHOUSE"), job(x.f.reviewer, "QUALITY")
    x.receive(q.tid, "8")
    lid = q.create(ctx=warehouse)
    with pytest.raises(BusinessError) as error:
        q.review(lid, ctx=warehouse)
    assert error.value.status == 403
    q.review(lid, ctx=quality)
    with pytest.raises(BusinessError) as error:
        q.post(lid, ctx=warehouse)
    assert error.value.status == 403
    q.post(lid, ctx=quality)
    assert not kinds(x.f, q.tid)
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal("92.032")


def test_warehouse_count_hands_off_to_quality_without_extra_permissions(counted):
    c = counted
    warehouse, quality = job(c.f.maker, "WAREHOUSE"), job(c.f.reviewer, "QUALITY")
    cid = c.create(ctx=warehouse)
    c.submit(cid, "8", ctx=warehouse)
    assert kinds(c.f, cid) == {"count_review"}
    with pytest.raises(BusinessError):
        c.review(cid, ctx=warehouse)
    c.review(cid, ctx=quality)
    assert kinds(c.f, cid) == {"count_post"}
    c.post(cid, ctx=quality)
    assert not kinds(c.f, cid) and c.stock() == 8


@pytest.mark.parametrize(
    "kind,permission",
    [
        (WorkKind.QUALITY_REVIEW, "quality.read"),
        (WorkKind.QUALITY_POST, "quality.read"),
        (WorkKind.TRANSFER_LOSS_REVIEW, "transfers.read"),
        (WorkKind.TRANSFER_LOSS_POST, "transfers.read"),
    ],
)
def test_inventory_exception_queue_respects_reader_tenant_entity_scope(flow, kind, permission):
    f = flow
    reader = replace(f.maker, permissions=frozenset({permission}))
    data = queue(f, kind, ctx=reader)
    assert data["items"] == []
    expected = 2 if permission == "quality.read" else 5
    assert len(data["kinds"]) == expected
    for ctx in [
        replace(reader, permissions=frozenset({"inventory.read"})),
        replace(reader, entity_ids=frozenset()),
        replace(reader, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            queue(f, kind, ctx=ctx)
    with pytest.raises(BusinessError):
        queue(f, kind, ctx=reader, entity=uuid4())


def test_quality_queue_http_permission_and_completed_disposition_disappears(api, isolated):
    client, owner = api
    q = isolated
    did = q.create()
    params = {"entity_id": str(q.x.w.entity), "kind": "quality_review"}
    assert client.get("/api/v1/work-queue", params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=ARRAY['quality.read'] "
                "WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": q.x.f.maker.tenant_id, "p": q.x.f.maker.principal_id},
        )
    response = client.get("/api/v1/work-queue", params=params)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["target_id"] == str(did)
    q.review(did, decision="REJECT")
    assert client.get("/api/v1/work-queue", params=params).json()["data"]["items"] == []
    assert q.stock("QUARANTINE") == 10
