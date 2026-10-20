from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_packaging import approved, line, receive_and_post
from test_procurement import flow as flow
from test_procurement import new_stock
from test_purchase_close import close, receipt_body

from medinv.entrypoints.core_api import create_app
from medinv.modules.procurement.api.schemas import OrderRevision, Transition
from medinv.modules.procurement.application import amendments
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def body(f, oid=None, qty="6", **changes):
    doc = f.service.detail(f.maker, oid or f.oid)
    return OrderRevision.model_validate(
        {
            "expected_version": doc["order"]["version"],
            "reason": "供应商确认修改采购约定",
            "supplier_id": doc["order"]["supplier_id"],
            "warehouse_id": doc["order"]["warehouse_id"],
            "lines": [{"sku_id": f.sku, "qty": qty, "unit_price": "12", "tax_rate": "0.13"}],
            **changes,
        }
    )


def revise(f, request=None, ctx=None, oid=None, key=None):
    return amendments.revise(
        f.service, ctx or f.maker, oid or f.oid, request or body(f, oid), key or str(uuid4())
    )


def approve_order(f, ctx=None, oid=None):
    target = oid or f.oid
    return f.service.transition(
        ctx or f.reviewer,
        target,
        Transition(
            expected_version=f.service.detail(f.maker, target)["order"]["version"],
            reason="逐项核对变更内容",
        ),
        str(uuid4()),
        True,
    )


def history(f, ctx=None, oid=None, offset=0):
    return amendments.history(f.service, ctx or f.maker, oid or f.oid, offset)


def test_revision_requires_reapproval_and_new_agreement_posts_correct_amounts(flow):
    f = flow
    before = f.detail()
    old_arrival = receipt_body(f, qty="6")
    result = revise(f)
    doc = f.detail()
    assert result["version"] == "3" and result["revision_no"] == 2
    assert doc["order"]["number"] == before["order"]["number"]
    assert doc["order"]["approval_state"] == "DRAFT"
    assert doc["order"]["approved_by"] is doc["order"]["approved_at"] is None
    assert doc["lines"][0]["id"] != before["lines"][0]["id"]
    assert Decimal(doc["lines"][0]["qty"]) == 6
    assert new_stock(f) == 0 and f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 0
    for action in [lambda: f.receive("1"), lambda: close(f), lambda: approve_order(f, f.maker)]:
        with pytest.raises(BusinessError):
            action()
    approve_order(f)
    old_arrival.expected_version = "4"
    with pytest.raises(BusinessError) as err:
        f.service.receive(f.maker, f.oid, old_arrival, str(uuid4()))
    assert err.value.code == "SOURCE_LINE_MISMATCH"
    arrival = f.receive("6")
    f.post(f.inspect(arrival["id"], "6"))
    assert new_stock(f) == 6
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 72
    events = history(f)["items"]
    revision = next(e for e in events if e["action"] == "purchase.revised")["detail"]
    assert revision["before"]["order"]["approved_by"] == str(f.reviewer.principal_id)
    assert Decimal(revision["before"]["lines"][0]["qty"]) == 10
    assert Decimal(revision["after"]["lines"][0]["unit_price"]) == 12
    approvals = [e for e in events if e["action"] == "purchase.approved"]
    assert [e["detail"]["revision_no"] for e in approvals] == [2, 1]


def test_all_current_round_editors_and_creator_are_barred_from_self_approval(flow):
    f = flow
    second = replace(f.maker, principal_id=uuid4())
    independent = replace(f.maker, principal_id=uuid4())
    revise(f, ctx=f.reviewer)
    revise(f, body(f, qty="7"), ctx=second)
    assert set(f.detail()["order"]["revision_editors"]) == {
        str(f.reviewer.principal_id),
        str(second.principal_id),
    }
    for actor in [f.maker, f.reviewer, second]:
        with pytest.raises(BusinessError) as err:
            approve_order(f, actor)
        assert err.value.code == "SEPARATION_REQUIRED"
    approve_order(f, independent)
    assert f.detail()["order"]["revision_editors"] == []
    revise(f, body(f, qty="8"), ctx=f.maker)
    approve_order(f, f.reviewer)  # Prior round editors may review a new independently edited round.


def test_draft_can_be_revised_and_cancelled_with_history(flow):
    f = flow
    oid = f.order(approve=False)
    revise(f, oid=oid)
    f.service.transition(
        f.maker, oid, Transition(expected_version="2", reason="停止采购"), str(uuid4()), False
    )
    assert f.service.detail(f.maker, oid)["order"]["approval_state"] == "CANCELLED"
    assert history(f, oid=oid)["items"][0]["action"] == "purchase.cancelled"


@pytest.mark.parametrize(
    "stage", ["PENDING", "REJECTED", "POSTED", "RECEIVED", "CLOSED", "CANCELLED"]
)
def test_received_closed_cancelled_orders_cannot_be_revised(flow, stage):
    f = flow
    if stage in {"PENDING", "REJECTED", "POSTED", "RECEIVED"}:
        rid = f.receive("10" if stage == "RECEIVED" else "4")["id"]
        if stage == "REJECTED":
            f.inspect(rid, "0", "4")
        if stage == "POSTED":
            f.post(f.inspect(rid, "4"))
    elif stage == "CLOSED":
        close(f)
    else:
        f.world.execute(
            "UPDATE med.purchase_order SET approval_state='CANCELLED' WHERE id=:id", id=f.oid
        )
    before = f.detail()
    with pytest.raises(BusinessError):
        revise(f)
    assert f.detail() == before


def test_packaging_revision_recalculates_totals_then_preserves_new_denominator(flow):
    f = flow
    package = approved(f)
    oid = f.order(lines=[line(f, package, qty="1", price="1")])
    request = body(f, oid, lines=[line(f, package, qty="2", price="1")])
    revise(f, request, oid=oid)
    doc = f.service.detail(f.maker, oid)
    assert Decimal(doc["lines"][0]["qty"]) == 6
    assert Decimal(doc["lines"][0]["trade_qty"]) == 2
    assert Decimal(doc["lines"][0]["gross_amount"]) == 2
    approve_order(f, oid=oid)
    receive_and_post(f, oid, "1")
    close(f, oid)
    doc = f.service.detail(f.maker, oid)
    assert Decimal(doc["lines"][0]["closed_qty"]) == 5
    assert doc["lines"][0]["package_snapshot"]["packaging_id"] == package["id"]
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == Decimal("0.33")
    # Net follows cumulative gross allocation: 1.77 * 0.33 / 2 = 0.29205 -> 0.29.
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("0.29")


def test_changes_supplier_warehouse_skus_and_lines_without_changing_identity(flow):
    f = flow
    supplier, warehouse, sku = [uuid4() for _ in range(3)]
    for table, old, new in [
        ("party", UUID(f.party["id"]), supplier),
        ("warehouse", f.world.warehouse, warehouse),
        ("sku", f.sku, sku),
    ]:
        f.world.execute(
            f"""INSERT INTO med.{table} SELECT
          (jsonb_populate_record(NULL::med.{table},to_jsonb(r)||
           jsonb_build_object('id',CAST(:new AS text),'code','REVISED'))).*
          FROM med.{table} r WHERE id=:old""",
            old=old,
            new=new,
        )
    # Replacement must not be blocked by the old supplier losing eligibility.
    f.world.execute("UPDATE med.party SET state='SUSPENDED' WHERE id=:id", id=UUID(f.party["id"]))
    request = body(
        f,
        supplier_id=supplier,
        warehouse_id=warehouse,
        lines=[
            {"sku_id": sku, "qty": "2", "unit_price": "3"},
            {"sku_id": f.sku, "qty": "1", "unit_price": "4"},
        ],
    )
    revise(f, request)
    approve_order(f)
    doc = f.detail()
    assert doc["order"]["supplier_id"] == str(supplier)
    assert doc["order"]["warehouse_id"] == str(warehouse)
    assert [r["sku_id"] for r in doc["lines"]] == [str(sku), str(f.sku)]
    assert all(Decimal(r["received_qty"]) == 0 for r in doc["lines"])


@pytest.mark.parametrize("issue", ["no_change", "quantity", "supplier", "warehouse", "frozen"])
def test_invalid_changes_leave_original_order_approval_and_audit_intact(flow, issue):
    f = flow
    request = body(f)
    if issue == "no_change":
        request = body(
            f,
            qty="10.000000",
            lines=[
                {"sku_id": f.sku, "qty": "10.000000", "unit_price": "13.0", "tax_rate": "0.1300"}
            ],
        )
    if issue == "quantity":
        request = body(f, qty="1.5")
    if issue == "supplier":
        request.supplier_id = uuid4()
    if issue == "warehouse":
        request.warehouse_id = uuid4()
    if issue == "frozen":
        f.world.execute("UPDATE med.stock_gate SET blocked=true")
    before, events = f.detail(), history(f)
    with pytest.raises(BusinessError):
        revise(f, request)
    assert f.detail() == before and history(f) == events


def test_commit_failure_rolls_back_replacement_lines_approval_version_and_audit(flow):
    f = flow
    before, events = f.detail(), history(f)
    request, key = body(f), str(uuid4())

    def revoke():
        raise BusinessError("SESSION_REVOKED", "提交前撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        revise(f, request, key=key)
    assert f.detail() == before and history(f) == events
    revise(f, request, key=key)
    assert f.detail()["order"]["revision_no"] == 2


@pytest.mark.parametrize("role", ["QUALITY", "FINANCE", "VIEWER"])
def test_readable_history_does_not_grant_revision_permission(flow, role):
    f = flow
    actor = replace(f.maker, permissions=ROLE_PERMISSIONS[role])
    assert history(f, ctx=actor)["items"]
    with pytest.raises(BusinessError) as err:
        revise(f, ctx=actor)
    assert err.value.status == 403


def test_revision_and_history_obey_tenant_entity_and_active_entity_scope(flow):
    f = flow
    for actor in [replace(f.maker, tenant_id=uuid4()), replace(f.maker, entity_ids=frozenset())]:
        with pytest.raises(BusinessError):
            revise(f, ctx=actor)
        with pytest.raises(BusinessError):
            history(f, ctx=actor)
    f.world.execute("UPDATE med.legal_entity SET status='SUSPENDED'")
    with pytest.raises(BusinessError) as err:
        revise(f)
    assert err.value.code == "ENTITY_UNAVAILABLE"


@pytest.mark.parametrize("competitor", ["same_key", "other_revision", "arrival"])
def test_concurrent_revision_is_idempotent_and_cannot_overlap_arrival(flow, competitor):
    f = flow
    request, key, barrier = body(f), str(uuid4()), Barrier(2)
    arrival = receipt_body(f, qty="1")

    def execute(index):
        barrier.wait(timeout=10)
        try:
            if index == 1 and competitor == "arrival":
                return f.service.receive(f.maker, f.oid, arrival, str(uuid4()))
            return revise(
                f,
                request
                if index == 0 or competitor == "same_key"
                else request.model_copy(update={"reason": "另一人同时修改"}),
                key=key if competitor == "same_key" else str(uuid4()),
            )
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [0, 1]))
    if competitor == "same_key":
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, dict) for r in results) == 1 and "VERSION_CONFLICT" in results
    count = f.world.scalar("SELECT count(*) FROM med.audit_event WHERE action='purchase.revised'")
    assert count == (0 if f.detail()["receipts"] else 1)


def test_history_pagination_and_received_database_guards(flow):
    f = flow
    for n in range(51):
        revise(f, body(f, qty=str(n + 20)))
    first, second = history(f), history(f, offset=50)
    assert len(first["items"]) == 50 and first["has_more"]
    assert len(second["items"]) == 3 and not second["has_more"]
    assert len({r["id"] for r in first["items"] + second["items"]}) == 53
    approve_order(f)
    f.receive("1")
    for sql in [
        "UPDATE med.purchase_order SET approval_state='DRAFT' WHERE id=:id",
        "UPDATE med.purchase_order SET revision_no=99 WHERE id=:id",
        "UPDATE med.purchase_order_line SET unit_price=1 WHERE order_id=:id",
        "UPDATE med.purchase_order_line SET qty=100 WHERE order_id=:id",
        "DELETE FROM med.purchase_order_line WHERE order_id=:id",
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql, id=f.oid)
    close(f)  # The guard must still permit closing unreceived quantities.


def test_revision_http_contract_auth_guard_and_default_switch(api, flow, settings):
    f = flow
    client, owner = api
    path = f"/api/v1/purchase-orders/{f.oid}/revise"
    request = body(f).model_dump(mode="json")
    assert client.post(path, json=request).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=:p "
                "WHERE tenant_id=:t AND principal_id=:a"
            ),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id, "a": f.maker.principal_id},
        )
    for headers in [{"X-CSRF-Token": "wrong"}, {"Origin": "https://wrong.example"}]:
        assert client.post(path, json=request, headers=headers).status_code == 403
    assert client.post(path, json={**request, "entity_id": str(uuid4())}).status_code == 422
    with TestClient(
        create_app(settings.model_copy(update={"procurement_commands_enabled": False}))
    ) as disabled:
        disabled.cookies.update(client.cookies)
        assert disabled.post(path, json=request, headers=dict(client.headers)).status_code == 503
    assert client.post(path, json=request).status_code == 200
    hpath = f"/api/v1/purchase-orders/{f.oid}/history"
    assert client.get(hpath).json()["data"]["items"][0]["action"] == "purchase.revised"
    assert client.get(hpath, params={"offset": -1}).status_code == 422
    client.cookies.clear()
    assert client.get(hpath).status_code == 401
    assert client.post(path, json=request).status_code == 401
