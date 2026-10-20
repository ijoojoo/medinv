from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_procurement import flow as flow
from test_procurement import new_stock
from test_purchase_close import close

from medinv.entrypoints.core_api import create_app
from medinv.modules.procurement.api.schemas import RejectedHandover
from medinv.modules.procurement.application.rejected_handover import hand_back
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def body(f, rid, qty="2", **changes):
    receipt = next(r for r in f.detail()["receipts"] if r["id"] == str(rid))
    return RejectedHandover.model_validate(
        {
            "expected_version": receipt["version"],
            "handover_ref": "HANDOVER-TEST",
            "recipient": "供应商委托承运人",
            "reason": "拒收包装破损实物已交接",
            "lines": [{"receipt_line_id": receipt["lines"][0]["id"], "qty": qty}],
            **changes,
        }
    )


def send(f, rid, request=None, ctx=None, key=None):
    return hand_back(
        f.service, ctx or f.maker, UUID(str(rid)), request or body(f, rid), key or str(uuid4())
    )


def test_partial_handover_closes_custody_without_posting_or_reopening_order(flow):
    f = flow
    rid = f.receive()["id"]
    inspected = f.inspect(rid, "6", "2")
    send(f, rid, body(f, rid, "1"))
    assert new_stock(f) == 0
    f.post(inspected)  # Handback does not block the previously qualified segment.
    ledger = f.world.scalar("SELECT count(*) FROM med.stock_ledger")
    cost = f.world.scalar("SELECT sum(amount) FROM med.cost_balance")
    payable = f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation")
    request, key = body(f, rid, "1"), str(uuid4())
    result = send(f, rid, request, key=key)
    assert send(f, rid, request, key=key) == result
    rec = f.detail()["receipts"][0]
    assert rec["lines"][0]["custody_qty"] == "2.000000"
    assert rec["lines"][0]["rejected_returned_qty"] == "2.000000"
    assert rec["lines"][0]["rejected_pending_qty"] == "0.000000"
    assert len(rec["rejected_handovers"]) == 2
    assert f.detail()["order"]["fulfillment_state"] == "RECEIVED"
    assert f.detail()["lines"][0]["received_qty"] == "10.000000"
    assert new_stock(f) == 6
    assert f.world.scalar("SELECT count(*) FROM med.stock_ledger") == ledger
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == cost
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == payable == 78
    assert f.world.scalar("SELECT count(*) FROM med.return_document") == 0
    f.post(f.inspect(rid, "2", "0"))  # Remaining uninspected arrivals still complete normally.
    assert new_stock(f) == 8
    assert f.detail()["receipts"][0]["lines"][0]["custody_qty"] == "0.000000"


@pytest.mark.parametrize(
    "state", ["uninspected", "qualified", "too_many", "already_returned", "fraction"]
)
def test_only_outstanding_rejected_quantities_can_be_handed_back(flow, state):
    f = flow
    rid = f.receive()["id"]
    if state == "qualified":
        f.inspect(rid, "10", "0")
    elif state != "uninspected":
        f.inspect(rid, "6", "4")
    if state == "already_returned":
        send(f, rid, body(f, rid, "4"))
    qty = "5" if state == "too_many" else "0.5" if state == "fraction" else "1"
    before = f.detail()
    with pytest.raises(BusinessError) as err:
        send(f, rid, body(f, rid, qty))
    assert err.value.code == (
        "INVALID_QUANTITY" if state == "fraction" else "REJECTED_QTY_EXCEEDED"
    )
    assert f.detail() == before


def test_closed_order_and_suspended_frozen_goods_can_be_returned(flow):
    f = flow
    rid = f.receive("4")["id"]
    f.inspect(rid, "0", "4")
    close(f)
    f.world.execute("UPDATE med.party SET state='SUSPENDED'")
    f.world.execute("UPDATE med.stock_gate SET blocked=true")
    f.world.execute("UPDATE med.lot SET quality_blocked=true")
    send(f, rid, body(f, rid, "4"))
    assert f.detail()["order"]["fulfillment_state"] == "CLOSED"
    assert f.detail()["lines"][0]["closed_qty"] == "6.000000"
    assert f.detail()["receipts"][0]["lines"][0]["custody_qty"] == "0.000000"
    assert new_stock(f) == 0


def test_scope_is_checked_before_idempotent_replay_and_facts_are_immutable(flow):
    f = flow
    rid = f.receive()["id"]
    f.inspect(rid, "6", "4")
    request, key = body(f, rid), str(uuid4())
    send(f, rid, request, key=key)
    for ctx in [
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
        replace(f.maker, entity_ids=frozenset({uuid4()})),
        replace(f.maker, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            send(f, rid, request, ctx=ctx, key=key)
    with pytest.raises(BusinessError) as err:
        send(f, rid, request.model_copy(update={"reason": "different"}), key=key)
    assert err.value.code == "IDEMPOTENCY_CONFLICT"
    for sql in [
        "UPDATE med.purchase_rejected_handover SET reason='changed'",
        "DELETE FROM med.purchase_rejected_handover_line",
        "UPDATE med.receipt_line SET rejected_returned_qty=5",
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_handback_cannot_return_twice(flow, same_key):
    f = flow
    rid = f.receive()["id"]
    f.inspect(rid, "6", "4")
    request, key, barrier = body(f, rid, "4"), str(uuid4()), Barrier(2)

    def attempt(index):
        barrier.wait(timeout=10)
        try:
            return send(f, rid, request, key=key if same_key or index == 0 else str(uuid4()))
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    if same_key:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        assert "VERSION_CONFLICT" in results
    assert f.world.scalar("SELECT sum(qty) FROM med.purchase_rejected_handover_line") == Decimal(4)


def test_other_receipt_lines_and_commit_revocation_roll_back_everything(flow):
    f = flow
    first, second = f.receive("5")["id"], f.receive("5", "B002")["id"]
    f.inspect(first, "1", "4")
    f.inspect(second, "1", "4")
    request = body(f, first)
    other = body(f, second).lines[0]
    invalid = request.model_copy(update={"lines": [request.lines[0], other]})
    with pytest.raises(BusinessError) as err:
        send(f, first, invalid)
    assert err.value.code == "NOT_FOUND"
    assert f.world.scalar("SELECT sum(rejected_returned_qty) FROM med.receipt_line") == 0

    def revoke():
        raise BusinessError("SESSION_REVOKED", "提交前会话已撤销", 401)

    token = before_business_commit.set(revoke)
    try:
        with pytest.raises(BusinessError, match="提交前"):
            send(f, first, request)
    finally:
        before_business_commit.reset(token)
    assert f.world.scalar("SELECT count(*) FROM med.purchase_rejected_handover") == 0
    assert f.world.scalar("SELECT sum(rejected_returned_qty) FROM med.receipt_line") == 0
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.audit_event WHERE action='purchase.rejected_handed_back'"
        )
        == 0
    )
    send(f, first, request)


def test_handover_http_permission_contract_and_write_switch(api, flow, settings):
    f = flow
    client, owner = api
    rid = f.receive()["id"]
    f.inspect(rid, "6", "4")
    request = body(f, rid).model_dump(mode="json")
    with pytest.raises(ValidationError):
        RejectedHandover.model_validate({**request, "lines": request["lines"] * 2})
    path = f"/api/v1/receipts/{rid}/rejected-handovers"
    assert client.post(path, json=request).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:p WHERE tenant_id=:t"),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id},
        )
    assert client.post(path, json=request, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(
            path, json={**request, "lines": [{**request["lines"][0], "qty": "0"}]}
        ).status_code
        == 422
    )
    with TestClient(
        create_app(settings.model_copy(update={"procurement_commands_enabled": False}))
    ) as disabled:
        disabled.cookies.update(client.cookies)
        assert disabled.post(path, json=request, headers=dict(client.headers)).status_code == 503
    assert client.post(path, json=request).status_code == 200
    assert client.post(path, json=request).status_code == 200
    assert f.world.scalar("SELECT count(*) FROM med.purchase_rejected_handover") == 1
