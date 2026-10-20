from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_retail import retail as retail
from test_retail_returns import retail_return as retail_return
from test_returns import ret as ret
from test_sales import sale as sale
from test_settlement import finance as finance

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.workspace.application import dashboard as dashboard_module
from medinv.modules.workspace.application.dashboard import WorkDashboard
from medinv.modules.workspace.application.queue import WorkKind, WorkQueue
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_period_uses_posted_sales_cost_and_real_cash_not_orders(finance):
    f = finance
    w = f.s.f.world
    service = WorkDashboard(w.uow)
    first = service.period(f.maker, w.entity, 7)
    assert len(first["series"]) == 7
    assert Decimal(first["current"]["sales"]) == 60
    assert Decimal(first["current"]["net"]) == Decimal("53.10")
    assert Decimal(first["current"]["profit"]) == Decimal("18.588")
    assert Decimal(first["current"]["cash"]) == 0
    assert all(Decimal(v) == 0 for v in first["previous"].values())
    assert first["start"] == str(f.s.f.day - timedelta(days=6))
    f.cash("80")
    current = service.period(f.maker, w.entity, 30)
    assert len(current["series"]) == 30
    assert Decimal(current["current"]["cash"]) == 80
    assert Decimal(current["current"]["sales"]) == 60
    assert sum(Decimal(r["sales"]) for r in current["series"]) == 60
    assert first["end"] == current["end"]
    voided = f.cash("11")["id"]
    f.service.void(
        f.maker, UUID(voided), Transition(expected_version="1", reason="测试错录作废"), str(uuid4())
    )
    assert Decimal(service.period(f.maker, w.entity, 7)["current"]["cash"]) == 80


def test_aging_boundaries_distinct_customers_and_drilldown_reconcile(finance, monkeypatch):
    f = finance
    f.s.post(f.s.ready("7"))
    service = WorkDashboard(f.s.f.world.uow)
    original = dashboard_module.one
    days = 30

    def shifted(repo, sql, params):
        result = original(repo, sql, params)
        if sql == "SELECT clock_timestamp() AS now":
            result["now"] += timedelta(days=days)
        return result

    monkeypatch.setattr(dashboard_module, "one", shifted)
    due_today = service.snapshot(f.maker, f.s.f.world.entity)
    assert Decimal(due_today["accounts"]["current"]) == 200
    assert due_today["accounts"]["overdue_customers"] == 0
    for elapsed, bucket in [(31, "late"), (60, "late"), (61, "overdue")]:
        days = elapsed
        snapshot = service.snapshot(f.maker, f.s.f.world.entity)
        details = service.attention(f.maker, f.s.f.world.entity, "overdue", 0)
        assert snapshot["accounts"]["overdue_customers"] == 1
        assert Decimal(snapshot["accounts"][bucket]) == Decimal(details["amount"]) == 200
        assert details["total"] == 2
        assert sum(Decimal(r["amount"]) for r in details["items"]) == 200
        assert {r["order_id"] for r in details["items"]} == {str(f.s.oid)}


def test_return_and_refund_reduce_totals_without_double_counting(ret):
    r = ret
    w = r.f.s.f.world
    cid = r.f.cash("80")["id"]
    allocation_id = r.f.allocate(cid)["allocation_ids"][0]
    r.f.reverse(allocation_id)
    rid = r.ready()
    r.post(rid)
    r.refund(rid, cid, "60")
    data = WorkDashboard(w.uow).period(r.f.maker, w.entity, 7)
    assert Decimal(data["current"]["sales"]) == 0
    assert Decimal(data["current"]["profit"]) == 0
    assert Decimal(data["current"]["cash"]) == 20


def test_retail_cash_uses_actual_amount_after_change_and_pending_refund(retail):
    r = retail
    service = WorkDashboard(r.f.world.uow)
    sale = r.create()["id"]
    r.pay(sale)  # Tendered 100, change 40; actual receipt is 60.
    paid = service.period(r.f.maker, r.f.world.entity, 7)
    assert Decimal(paid["current"]["sales"]) == 0
    assert Decimal(paid["current"]["cash"]) == 60
    r.refund(sale)
    refunded = service.period(r.f.maker, r.f.world.entity, 7)
    assert Decimal(refunded["current"]["cash"]) == 0


def test_completed_retail_and_partial_return_flow_into_same_period(retail_return):
    x = retail_return
    service = WorkDashboard(x.world.uow)
    initial = service.period(x.f.maker, x.world.entity, 7)
    assert Decimal(initial["current"]["sales"]) == 60
    assert Decimal(initial["current"]["cash"]) == 60
    rid = x.ready("1")
    x.post(rid)
    x.refund(rid)
    after = service.period(x.f.maker, x.world.entity, 7)
    assert Decimal(after["current"]["sales"]) == 40
    assert Decimal(after["current"]["cash"]) == 40


def test_snapshot_tracks_allocations_and_does_not_leak_inventory(finance):
    f = finance
    w = f.s.f.world
    service = WorkDashboard(w.uow)
    before = service.snapshot(f.maker, w.entity)
    accounts = before["accounts"]
    assert Decimal(accounts["receivable"]) == 60
    assert Decimal(accounts["payable"]) == 130
    assert sum(Decimal(accounts[k]) for k in ("current", "late", "overdue")) == 60
    assert Decimal(before["inventory"]["amount"]) == Decimal("80.528")
    assert sum(Decimal(g["amount"]) for g in before["inventory"]["groups"]) == Decimal("80.528")
    assert before["payable_due_status"] == before["slow_stock_status"] == "unavailable"
    f.allocate(f.cash()["id"], "20")
    after = service.snapshot(f.maker, w.entity)
    assert Decimal(after["accounts"]["receivable"]) == 40
    assert after["inventory"] == before["inventory"]
    finance_only = replace(f.maker, permissions=frozenset({"finance.read"}))
    assert service.snapshot(finance_only, w.entity)["inventory"] is None
    with pytest.raises(BusinessError, match="权限"):
        service.attention(finance_only, w.entity, "near", 0)


def test_near_expiry_details_reconcile_exact_decimal_amount_and_original_sku(flow):
    f = flow
    receipt = f.receive(
        extra=[
            {
                "order_line_id": f.detail()["lines"][0]["id"],
                "location_id": f.world.location,
                "qty": "10",
                "lot_no": "NEAR",
                "mfg_date": f.day - timedelta(days=1),
                "expiry_date": f.day + timedelta(days=10),
            }
        ]
    )
    f.post(f.inspect(receipt["id"]))
    service = WorkDashboard(f.world.uow)
    snap = service.snapshot(f.maker, f.world.entity)
    detail = service.attention(f.maker, f.world.entity, "near", 0)
    assert detail["total"] == snap["inventory"]["near_skus"] == 1
    assert (
        Decimal(detail["amount"]) == Decimal(snap["inventory"]["near_amount"]) == Decimal("115.04")
    )
    assert detail["items"][0]["number"] == "S-NEW"
    assert detail["items"][0]["lot_no"] == "NEAR"
    assert detail["items"][0]["day"] == str(f.day + timedelta(days=10))
    assert isinstance(detail["items"][0]["amount"], str)
    assert detail["has_more"] is False
    assert service.attention(f.maker, f.world.entity, "near", 20)["items"] == []
    assert service.attention(f.maker, f.world.entity, "expired", 0)["total"] == 0
    assert service.attention(f.maker, f.world.entity, "overdue", 0)["total"] == 0


def test_queue_summary_counts_whole_filtered_set_and_risk_only_exception_stages(flow):
    f = flow
    for _ in range(7):
        f.order()
    queue = WorkQueue(f.world.uow)
    first = queue.listing(f.maker, f.world.entity, WorkKind.ALL, "", 0, limit=6)
    assert first["total"] == first["counts"]["purchase_receive"] == 8
    assert len(first["items"]) == 6 and first["has_more"]
    second = queue.listing(f.maker, f.world.entity, WorkKind.ALL, "", 6, limit=6)
    assert len(second["items"]) == 2 and not second["has_more"]
    assert not ({x["item_id"] for x in first["items"]} & {x["item_id"] for x in second["items"]})
    number = second["items"][0]["number"]
    found = queue.listing(f.maker, f.world.entity, WorkKind.ALL, number, 0)
    assert found["total"] == sum(found["counts"].values()) == 1
    urgent = queue.listing(f.maker, f.world.entity, WorkKind.ALL, "", 0, priority=True)
    assert urgent["total"] == urgent["priority_count"] == 0
    service = WorkDashboard(f.world.uow)
    initial = service.risks(f.maker, f.world.entity, WorkKind.ALL, "", 0)
    assert initial["total_open"] == 0  # Approved purchases are routine work, not risk.
    f.inspect(f.receive()["id"], "8", "2")
    risks = service.risks(f.maker, f.world.entity, WorkKind.ALL, "", 0)
    assert risks["total_open"] == risks["total"] == sum(g["count"] for g in risks["groups"]) == 1
    assert risks["items"][0]["kind"] == "purchase_handback"
    assert risks["history_status"] == risks["assignment_status"] == "unavailable"


def test_workbench_permissions_entity_scope_and_tenant_rls(flow):
    f = flow
    service = WorkDashboard(f.world.uow)
    forbidden = replace(f.maker, permissions=frozenset({"purchase.read"}))
    with pytest.raises(BusinessError) as error:
        service.period(forbidden, f.world.entity, 7)
    assert error.value.status == 403
    for context, entity in [
        (f.maker, uuid4()),
        (replace(f.maker, tenant_id=uuid4()), f.world.entity),
    ]:
        for read in [
            lambda c=context, e=entity: service.period(c, e, 7),
            lambda c=context, e=entity: service.snapshot(c, e),
            lambda c=context, e=entity: service.attention(c, e, "overdue", 0),
        ]:
            with pytest.raises(BusinessError) as error:
                read()
            assert error.value.status == 404


def test_http_workbench_bounds_no_store_permissions_and_stale_context(api, flow):
    client, owner = api
    f = flow
    params = {"entity_id": str(f.world.entity)}
    assert client.get("/api/v1/workbench/period", params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership
            SET permissions=ARRAY['finance.read','inventory.read','purchase.read']
            WHERE tenant_id=:t AND principal_id=:a"""),
            {"t": f.maker.tenant_id, "a": f.maker.principal_id},
        )
    for route, extra in [
        ("period", {"days": "7"}),
        ("period", {"days": "30"}),
        ("snapshot", {}),
        ("attention", {"kind": "near"}),
        ("risks", {}),
    ]:
        response = client.get("/api/v1/workbench/" + route, params={**params, **extra})
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/workbench/period", params={**params, "days": 8}).status_code == 422
    assert (
        client.get("/api/v1/workbench/risks", params={**params, "group": "invalid"}).status_code
        == 422
    )
    client.headers["X-Context-Generation"] = "2"
    assert client.get("/api/v1/workbench/snapshot", params=params).status_code == 409
