from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from test_api import api as api
from test_procurement import flow as flow
from test_retail import retail as retail
from test_sales import sale as sale

from medinv.modules.procurement.api.schemas import RejectedHandover, Transition
from medinv.modules.procurement.application.rejected_handover import hand_back
from medinv.modules.sales.application.fulfillment import cancel
from medinv.modules.workspace.application.queue import WorkKind, WorkQueue
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def queue(f, kind=WorkKind.ALL, search="", offset=0, ctx=None, entity=None):
    return WorkQueue(f.world.uow).listing(
        ctx or f.maker, entity or f.world.entity, kind, search, offset
    )


def kinds(f, target):
    return {r["kind"] for r in queue(f)["items"] if r["target_id"] == str(target)}


def test_purchase_draft_approval_and_cancel_change_read_only_queue(flow):
    f = flow
    oid = f.order(approve=False)
    before = f.world.scalar("SELECT count(*) FROM med.audit_event")
    assert kinds(f, oid) == {"purchase_approve"}
    assert f.world.scalar("SELECT count(*) FROM med.audit_event") == before
    f.service.transition(
        f.reviewer, oid, Transition(expected_version="1", reason="独立审核"), str(uuid4()), True
    )
    assert kinds(f, oid) == {"purchase_receive"}
    cancelled = f.order(approve=False)
    f.service.transition(
        f.maker,
        cancelled,
        Transition(expected_version="1", reason="取消错误单"),
        str(uuid4()),
        False,
    )
    assert kinds(f, cancelled) == set()


@pytest.mark.parametrize("qty", ["4", "10"])
def test_closed_or_fully_received_purchase_keeps_inspection_posting_and_handback(flow, qty):
    f = flow
    rid = f.receive(qty)["id"]
    if qty == "4":
        f.service.close(
            f.maker,
            f.oid,
            Transition(expected_version=f.detail()["order"]["version"], reason="关闭余量"),
            str(uuid4()),
        )
    assert kinds(f, f.oid) == {"purchase_inspect"}
    inspected = f.inspect(rid, "2", "1")
    assert kinds(f, f.oid) == {"purchase_inspect", "purchase_post", "purchase_handback"}
    post_rows = queue(f, WorkKind.PURCHASE_POST)["items"]
    assert post_rows[0]["item_id"] == inspected["receipt_posting_id"]
    assert post_rows[0]["related_number"] == f.detail()["receipts"][0]["number"]
    f.post(inspected)
    assert kinds(f, f.oid) == {"purchase_inspect", "purchase_handback"}
    last = f.inspect(rid, str(int(qty) - 3))
    rec = f.detail()["receipts"][0]
    hand_back(
        f.service,
        f.maker,
        UUID(rid),
        RejectedHandover(
            expected_version=rec["version"],
            handover_ref="RETURN",
            recipient="供应商交接人",
            reason="拒收实物退回",
            lines=[{"receipt_line_id": rec["lines"][0]["id"], "qty": "1"}],
        ),
        str(uuid4()),
    )
    assert kinds(f, f.oid) == {"purchase_post"}
    f.post(last)
    assert kinds(f, f.oid) == set()


def test_each_unposted_inspection_segment_has_its_own_work_item(flow):
    f = flow
    rid = f.receive()["id"]
    first = f.inspect(rid, "4")
    second = f.inspect(rid, "6")
    assert {r["item_id"] for r in queue(f, WorkKind.PURCHASE_POST)["items"]} == {
        first["receipt_posting_id"],
        second["receipt_posting_id"],
    }
    f.post(first)
    assert [r["item_id"] for r in queue(f, WorkKind.PURCHASE_POST)["items"]] == [
        second["receipt_posting_id"]
    ]


def test_sales_partial_shipments_move_between_work_stages_and_disappear_when_complete(sale):
    s, f = sale, sale.f
    draft = s.order("1", approve=False)
    assert kinds(f, draft) == {"sales_approve"}
    assert kinds(f, s.oid) == {"sales_allocate"}
    for qty in ("3", "7"):
        sid = s.allocate(qty)["id"]
        unallocated = {"sales_allocate"} if qty == "3" else set()
        assert kinds(f, s.oid) == unallocated | {"sales_pick"}
        s.pick(sid)
        assert kinds(f, s.oid) == unallocated | {"sales_verify"}
        s.verify(sid)
        assert kinds(f, s.oid) == unallocated | {"sales_post"}
        s.post(sid)
        assert kinds(f, s.oid) == unallocated


def test_expired_unpicked_shipment_remains_visible_until_cancel_then_demand_reappears(sale):
    s, f = sale, sale.f
    sid = s.allocate()["id"]
    f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 second'")
    item = queue(f, WorkKind.SALES_PICK)["items"][0]
    assert item["reservation_expired"] is True and item["target_id"] == str(s.oid)
    assert item["item_id"] == sid and item["related_number"].startswith("SH-")
    cancel(
        s.service,
        f.maker,
        UUID(sid),
        Transition(expected_version="1", reason="未拣货取消"),
        str(uuid4()),
    )
    assert kinds(f, s.oid) == {"sales_allocate"}


@pytest.mark.parametrize("verified", [False, True])
def test_picked_or_verified_shipment_keeps_protected_reservation_without_expiry_warning(
    sale, verified
):
    s, f = sale, sale.f
    sid = s.allocate()["id"]
    s.pick(sid)
    if verified:
        s.verify(sid)
    f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 second'")
    stage = WorkKind.SALES_POST if verified else WorkKind.SALES_VERIFY
    item = queue(f, stage)["items"][0]
    assert item["item_id"] == sid and item["reservation_expired"] is False


@pytest.mark.parametrize("terminal", ["complete", "refund"])
def test_paid_retail_remains_pending_until_delivery_or_actual_refund(retail, terminal):
    r, f = retail, retail.f
    sid = r.create()["id"]
    assert kinds(f, sid) == {"retail_pay"}
    r.pay(sid)
    assert kinds(f, sid) == {"retail_complete"}
    # A paid order's old reservation deadline is not a prompt to collect payment again.
    f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 second'")
    assert queue(f, WorkKind.RETAIL_COMPLETE)["items"][0]["reservation_expired"] is False
    (r.complete if terminal == "complete" else r.refund)(sid)
    assert kinds(f, sid) == set()


def test_unpaid_retail_expiry_is_not_silently_hidden(retail):
    r, f = retail, retail.f
    sid = r.create()["id"]
    f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 second'")
    assert queue(f, WorkKind.RETAIL_PAY)["items"][0]["reservation_expired"] is True
    r.service.cancel(
        f.maker, UUID(sid), Transition(expected_version="1", reason="未付款取消"), str(uuid4())
    )
    assert kinds(f, sid) == set()


def test_work_queue_filters_each_module_permission_and_entity_scope(sale):
    f = sale.f
    for permission, prefix, amount in [
        ("purchase.read", "purchase_", 5),
        ("sales.read", "sales_", 5),
        ("retail.read", "retail_", 2),
    ]:
        ctx = replace(f.maker, permissions=frozenset([permission]))
        data = queue(f, ctx=ctx)
        assert len(data["kinds"]) == amount
        assert all(r["kind"].startswith(prefix) for r in data["items"])
        forbidden = WorkKind.SALES_ALLOCATE if prefix != "sales_" else WorkKind.PURCHASE_RECEIVE
        with pytest.raises(BusinessError) as error:
            queue(f, forbidden, ctx=ctx)
        assert error.value.status == 403
    for ctx in [
        replace(f.maker, permissions=frozenset()),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            queue(f, ctx=ctx)
    with pytest.raises(BusinessError):
        queue(f, entity=uuid4())


def test_work_queue_literal_search_pagination_and_stable_tie_breaks(flow):
    f = flow
    for _ in range(51):
        f.order(approve=False)
    first = queue(f, WorkKind.PURCHASE_APPROVE)
    second = queue(f, WorkKind.PURCHASE_APPROVE, offset=50)
    assert len(first["items"]) == 50 and first["has_more"]
    assert len(second["items"]) == 1 and not second["has_more"]
    assert not ({r["item_id"] for r in first["items"]} & {r["item_id"] for r in second["items"]})
    target = first["items"][0]
    assert queue(f, search=target["number"].lower())["items"] == [target]
    assert len(queue(f, WorkKind.PURCHASE_APPROVE, search="测试供应商")["items"]) == 50
    assert queue(f, search="%_")["items"] == []
    assert queue(f, search="' OR 1=1 --")["items"] == []
    rid = f.receive()["id"]
    rec = f.detail()["receipts"][0]
    assert queue(f, search=rec["number"])["items"][0]["item_id"] == rid


@pytest.mark.parametrize("kind", [WorkKind.ALL, WorkKind.PURCHASE_RECEIVE])
def test_pooled_queue_replans_per_tenant_without_changing_connection_defaults(flow, settings, kind):
    f = flow
    # Force the connection's default to generic plans, prepare on the first call,
    # and reuse one physical connection across differently populated tenants.
    pooled = create_engine(
        settings.database_url.get_secret_value(),
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
        connect_args={"prepare_threshold": 0, "options": "-c plan_cache_mode=force_generic_plan"},
    )
    uow = TenantUnitOfWork(pooled, settings)
    empty_entity = uuid4()
    empty = replace(f.maker, tenant_id=uuid4(), entity_ids=frozenset([empty_entity]))
    try:
        with uow.open(empty) as session:
            session.execute(
                text("""INSERT INTO med.legal_entity
                (tenant_id,id,created_by,updated_by,name,credit_code,status)
                VALUES (:t,:e,:a,:a,'空白待办主体','TEST','ACTIVE')"""),
                {"t": empty.tenant_id, "e": empty_entity, "a": empty.principal_id},
            )
        service = WorkQueue(uow)
        for _ in range(4):
            assert service.listing(empty, empty_entity, kind, "", 0)["items"] == []
            rows = service.listing(f.maker, f.world.entity, kind, "", 0)["items"]
            assert [r["target_id"] for r in rows] == [str(f.oid)]
        with pooled.connect() as conn:
            # The forced policy is limited to the read-only listing transaction.
            assert conn.execute(text("SHOW plan_cache_mode")).scalar_one() == "force_generic_plan"
            plans = (
                conn.execute(
                    text("""SELECT generic_plans,custom_plans FROM pg_prepared_statements
                WHERE statement LIKE 'WITH work AS MATERIALIZED %'
                  AND statement LIKE '%selected AS MATERIALIZED%'""")
                )
                .mappings()
                .all()
            )
            assert len(plans) == 1
            assert plans[0]["generic_plans"] == 0
            assert plans[0]["custom_plans"] == 8
    finally:
        pooled.dispose()


def test_http_queue_auth_enum_bounds_no_store_and_stale_context(api, flow):
    client, owner = api
    f = flow
    params = {"entity_id": str(f.world.entity)}
    # The basic fixture can read inventory, hence opening work only.
    initial = client.get("/api/v1/work-queue", params=params)
    assert initial.status_code == 200
    assert initial.json()["data"]["items"] == []
    assert {k["code"] for k in initial.json()["data"]["kinds"]} == {
        "opening_review",
        "opening_post",
    }
    assert (
        client.get("/api/v1/work-queue", params={**params, "kind": "purchase_approve"}).status_code
        == 403
    )
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['purchase.read']
            WHERE tenant_id=:t AND principal_id=:a"""),
            {"t": f.maker.tenant_id, "a": f.maker.principal_id},
        )
    response = client.get("/api/v1/work-queue", params=params)
    assert response.status_code == 200, response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["items"][0]["target_id"] == str(f.oid)
    for bad in [{"kind": "arbitrary"}, {"offset": -1}, {"offset": 100001}, {"search": "x" * 101}]:
        assert client.get("/api/v1/work-queue", params={**params, **bad}).status_code == 422
    assert (
        client.get("/api/v1/work-queue", params={**params, "kind": "retail_pay"}).status_code == 403
    )
    assert (
        client.get("/api/v1/work-queue", params={**params, "entity_id": str(uuid4())}).status_code
        == 404
    )
    client.headers["X-Context-Generation"] = "2"
    assert client.get("/api/v1/work-queue", params=params).status_code == 409
