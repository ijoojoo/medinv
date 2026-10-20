from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_packaging import approved, line
from test_procurement import flow as flow
from test_procurement import new_stock

from medinv.entrypoints.core_api import create_app
from medinv.modules.procurement.api.schemas import InspectionCreate, ReceiptCreate, Transition
from medinv.modules.returns.api.schemas import ReturnCreate, ReturnReview
from medinv.modules.returns.application import posting as return_posting
from medinv.modules.returns.application.service import ReturnService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def close(f, oid=None, body=None, key=None, ctx=None):
    target = oid or f.oid
    return f.service.close(
        ctx or f.maker,
        target,
        body
        or Transition(
            expected_version=f.service.detail(f.maker, target)["order"]["version"],
            reason="供应商确认剩余数量停止交付",
        ),
        key or str(uuid4()),
    )


def receipt_body(f, oid=None, qty="6"):
    doc = f.service.detail(f.maker, oid or f.oid)
    return ReceiptCreate(
        expected_version=doc["order"]["version"],
        shipment_ref="CLOSE-TEST",
        transport_note="关闭采购余量验收",
        lines=[
            {
                "order_line_id": doc["lines"][0]["id"],
                "location_id": f.world.location,
                "qty": qty,
                "lot_no": "CLOSE-BATCH",
                "mfg_date": f.day - timedelta(days=1),
                "expiry_date": f.day + timedelta(days=365),
            }
        ],
    )


def test_unreceived_approved_order_closes_without_inventory_cost_or_payable(flow):
    f = flow
    result = close(f)
    doc = f.detail()
    assert result["fulfillment_state"] == "CLOSED"
    assert doc["order"]["approval_state"] == "APPROVED"
    assert doc["order"]["closed_by"] == str(f.maker.principal_id)
    assert doc["order"]["closed_at"] == result["closed_at"]
    assert doc["order"]["close_reason"] == "供应商确认剩余数量停止交付"
    assert doc["lines"][0]["qty"] == "10.000000"
    assert doc["lines"][0]["closed_qty"] == "10.000000"
    assert doc["lines"][0]["remaining_qty"] == "0.000000"
    assert doc["lines"][0]["unreceived_qty"] == "10.000000"
    assert new_stock(f) == 0
    for table in ["receipt", "posting_batch", "cost_balance", "payable_obligation"]:
        assert f.world.scalar(f"SELECT count(*) FROM med.{table}") == 0
    with pytest.raises(BusinessError) as err:
        f.receive("1")
    assert err.value.code == "INVALID_STATE"
    assert (
        f.world.scalar("SELECT count(*) FROM med.audit_event WHERE action='purchase.closed'") == 1
    )


@pytest.mark.parametrize("stage", ["PENDING", "QUALIFIED", "POSTED", "REJECTED"])
def test_closure_keeps_received_custody_and_allows_original_inspection_posting(flow, stage):
    f = flow
    rid = f.receive("6")["id"]
    inspected = None
    if stage == "REJECTED":
        f.inspect(rid, "0", "6")
    elif stage != "PENDING":
        inspected = f.inspect(rid, "4", "2")
        if stage == "POSTED":
            f.post(inspected)
    before = new_stock(f)
    close(f)
    assert new_stock(f) == before
    doc = f.detail()
    assert doc["lines"][0]["received_qty"] == "6.000000"
    assert doc["lines"][0]["closed_qty"] == "4.000000"
    assert doc["receipts"][0]["lines"][0]["qty"] == "6.000000"
    if stage == "PENDING":
        inspected = f.inspect(rid, "4", "2")
    if stage in {"PENDING", "QUALIFIED"}:
        f.post(inspected)
    doc = f.detail()
    assert doc["order"]["fulfillment_state"] == "CLOSED"
    assert new_stock(f) == (0 if stage == "REJECTED" else 4)
    assert Decimal(doc["receipts"][0]["lines"][0]["custody_qty"]) == (
        6 if stage == "REJECTED" else 2
    )
    if stage != "REJECTED":
        assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 52
        assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("46.02")


def test_multiline_close_saves_only_each_line_unreceived_remainder(flow):
    f = flow
    oid = f.order(lines=[{"sku_id": f.sku, "qty": "5", "unit_price": "13"} for _ in range(2)])
    doc = f.service.detail(f.maker, oid)
    body = receipt_body(f, oid, "5")
    body.lines.append(
        body.lines[0].model_copy(
            update={"order_line_id": UUID(doc["lines"][1]["id"]), "qty": "2.000000"}
        )
    )
    f.service.receive(f.maker, oid, body, str(uuid4()))
    result = close(f, oid)
    doc = f.service.detail(f.maker, oid)
    assert [r["closed_qty"] for r in doc["lines"]] == ["0.000000", "3.000000"]
    assert [r["remaining_qty"] for r in doc["lines"]] == ["0.000000", "0.000000"]
    assert result["lines"] == [{"id": doc["lines"][1]["id"], "closed_qty": "3.000000"}]


def test_packaging_close_keeps_original_price_denominator_for_later_posting(flow):
    f = flow
    pack = approved(f)
    oid = f.order(lines=[line(f, pack, qty="1", price="1")])  # 1 box = 3 items, 1 CNY total.
    f.service.receive(f.maker, oid, receipt_body(f, oid, "1"), str(uuid4()))
    original = f.service.detail(f.maker, oid)["lines"][0]
    close(f, oid)
    doc = f.service.detail(f.maker, oid)
    row, rec = doc["lines"][0], doc["receipts"][0]
    for field in [
        "qty",
        "unit_price",
        "trade_qty",
        "package_snapshot",
        "gross_amount",
        "net_amount",
    ]:
        assert row[field] == original[field]
    assert row["closed_qty"] == "2.000000"
    inspected = f.service.inspect(
        f.reviewer,
        UUID(rec["id"]),
        InspectionCreate(
            expected_version=rec["version"],
            note="关闭后验收已到货部分",
            lines=[
                {
                    "receipt_line_id": rec["lines"][0]["id"],
                    "qualified_qty": "1",
                    "rejected_qty": "0",
                }
            ],
        ),
        str(uuid4()),
    )
    f.post(inspected)
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == Decimal("0.33")
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("0.29")


@pytest.mark.parametrize("state", ["DRAFT", "CANCELLED", "RECEIVED", "CLOSED"])
def test_closure_rejects_wrong_states(flow, state):
    f = flow
    if state in {"DRAFT", "CANCELLED"}:
        oid = f.order(approve=False)
        if state == "CANCELLED":
            f.service.transition(
                f.maker,
                oid,
                Transition(expected_version="1", reason="取消草稿"),
                str(uuid4()),
                False,
            )
    else:
        oid = f.oid
        if state == "RECEIVED":
            f.receive()
        else:
            close(f)
    with pytest.raises(BusinessError) as err:
        close(f, oid)
    assert err.value.code == "INVALID_STATE"


def test_expired_supplier_suspended_product_and_freeze_do_not_prevent_closure(flow):
    f = flow
    f.world.execute("UPDATE med.party SET state='SUSPENDED'")
    f.world.execute("UPDATE med.product SET approval_state='SUSPENDED'")
    f.world.execute("UPDATE med.stock_gate SET blocked=true")
    assert close(f)["fulfillment_state"] == "CLOSED"


def test_same_key_concurrent_close_is_replayed_once_and_changed_body_rejected(flow):
    f = flow
    body = Transition(expected_version="2", reason="停止余量")
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: close(f, body=body, key=key), range(3)))
    assert all(result == results[0] for result in results)
    assert (
        f.world.scalar("SELECT count(*) FROM med.audit_event WHERE action='purchase.closed'") == 1
    )
    with pytest.raises(BusinessError) as err:
        close(f, body=body.model_copy(update={"reason": "另一个原因"}), key=key)
    assert err.value.code == "IDEMPOTENCY_CONFLICT"


def test_arrival_and_close_race_has_one_version_winner_and_keeps_quantity_conservation(flow):
    f = flow
    receipt = receipt_body(f)
    body = Transition(expected_version="2", reason="并发关闭")

    def attempt(kind):
        try:
            if kind == "close":
                close(f, body=body)
            else:
                f.service.receive(f.maker, f.oid, receipt, str(uuid4()))
            return "OK"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["close", "receive"]))
    assert sorted(results) == ["OK", "VERSION_CONFLICT"]
    if f.detail()["order"]["fulfillment_state"] == "OPEN":
        close(f)
    row = f.detail()["lines"][0]
    assert Decimal(row["received_qty"]) + Decimal(row["closed_qty"]) == 10


def test_posting_and_close_can_both_complete_without_changing_original_payable(flow):
    f = flow
    inspected = f.inspect(f.receive("4")["id"], "4")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close, f), pool.submit(f.post, inspected)]
        for future in futures:
            future.result(timeout=10)
    assert f.detail()["order"]["fulfillment_state"] == "CLOSED"
    assert f.detail()["lines"][0]["closed_qty"] == "6.000000"
    assert new_stock(f) == 4
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 52


def test_purchase_return_after_closure_uses_original_posting_and_never_reopens_order(flow):
    f = flow
    source = f.post(f.inspect(f.receive("4")["id"], "4"))
    close(f)
    svc = ReturnService(f.world.uow)
    source_id = UUID(source["posting_id"])
    source_line = svc.source_detail(f.maker, source_id)["lines"][0]
    rid = UUID(
        svc.create(
            f.maker,
            ReturnCreate(
                client_operation_id=uuid4(),
                source_posting_id=source_id,
                reason="关闭后退回已入库部分",
                lines=[{"source_posting_line_id": source_line["id"], "qty": "2"}],
            ),
            str(uuid4()),
        )["id"]
    )
    svc.review(
        f.reviewer,
        rid,
        ReturnReview(
            expected_version="1",
            decision="APPROVE",
            reason="核对原批次与实物",
            lines=[
                {
                    "source_posting_line_id": source_line["id"],
                    "qty": "2",
                    "lot_no": source_line["lot_no"],
                }
            ],
        ),
        str(uuid4()),
    )
    return_posting.post(
        svc, f.reviewer, rid, Transition(expected_version="2", reason="已退还供应商"), str(uuid4())
    )
    assert new_stock(f) == 2
    assert f.detail()["order"]["fulfillment_state"] == "CLOSED"
    assert f.detail()["lines"][0]["received_qty"] == "4.000000"
    assert f.detail()["lines"][0]["closed_qty"] == "6.000000"


def test_commit_failure_rolls_back_closed_quantities_header_audit_and_retry_record(flow):
    f = flow
    f.receive("3")
    before = f.detail()
    events = f.world.scalar("SELECT count(*) FROM med.audit_event")
    keys = f.world.scalar("SELECT count(*) FROM med.idempotency_record")

    def revoke():
        raise BusinessError("SESSION_REVOKED", "模拟提交前撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        close(f)
    assert f.detail() == before
    assert f.world.scalar("SELECT count(*) FROM med.audit_event") == events
    assert f.world.scalar("SELECT count(*) FROM med.idempotency_record") == keys
    assert close(f)["fulfillment_state"] == "CLOSED"


@pytest.mark.parametrize("role", ["VIEWER", "QUALITY", "FINANCE"])
def test_readers_and_quality_reviewer_cannot_close_without_purchase_write(flow, role):
    with pytest.raises(BusinessError) as err:
        close(flow, ctx=replace(flow.maker, permissions=ROLE_PERMISSIONS[role]))
    assert err.value.status == 403
    assert flow.detail()["order"]["fulfillment_state"] == "OPEN"


def test_scope_and_tenant_fencing_and_inactive_entity(flow):
    f = flow
    for ctx in [replace(f.maker, entity_ids=frozenset()), replace(f.maker, tenant_id=uuid4())]:
        with pytest.raises(BusinessError):
            close(f, ctx=ctx)
    f.world.execute("UPDATE med.legal_entity SET status='SUSPENDED'")
    with pytest.raises(BusinessError) as err:
        close(f)
    assert err.value.code == "ENTITY_UNAVAILABLE"


def test_database_guards_preserve_closed_fact_and_prevent_quantity_erasure(flow):
    f = flow
    f.receive("3")
    with pytest.raises(DBAPIError):
        f.world.execute(
            "UPDATE med.purchase_order_line SET closed_qty=8 WHERE order_id=:o", o=f.oid
        )
    close(f)
    for sql in [
        "UPDATE med.purchase_order SET fulfillment_state='OPEN' WHERE id=:o",
        "UPDATE med.purchase_order SET close_reason='改写原因' WHERE id=:o",
        "UPDATE med.purchase_order_line SET closed_qty=0 WHERE order_id=:o",
        "UPDATE med.purchase_order_line SET received_qty=4 WHERE order_id=:o",
        "DELETE FROM med.purchase_order_line WHERE order_id=:o",
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql, o=f.oid)


def test_close_http_authentication_csrf_contract_permission_and_default_switch(api, flow, settings):
    f = flow
    client, owner = api
    path = f"/api/v1/purchase-orders/{f.oid}/close"
    body = {"expected_version": "2", "reason": "接口关闭验证"}
    assert client.post(path, json=body).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=:p "
                "WHERE tenant_id=:t AND principal_id=:a"
            ),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id, "a": f.maker.principal_id},
        )
    assert client.post(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(path, json=body, headers={"Origin": "https://wrong.example"}).status_code == 403
    )
    assert client.post(path, json={**body, "tenant_id": str(uuid4())}).status_code == 422
    from medinv.platform.config import Settings

    assert Settings.model_fields["procurement_commands_enabled"].default is False
    with TestClient(
        create_app(settings.model_copy(update={"procurement_commands_enabled": False}))
    ) as disabled:
        disabled.cookies.update(client.cookies)
        response = disabled.post(path, json=body, headers=dict(client.headers))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/purchase-orders/{id}/close"][
        "post"
    ]
    assert {p["name"] for p in operation["parameters"] if p["in"] == "header"} >= {
        "Idempotency-Key",
        "X-CSRF-Token",
        "X-Context-Generation",
    }
    assert client.post(path, json=body).status_code == 200
    assert f.detail()["order"]["fulfillment_state"] == "CLOSED"
    client.cookies.clear()
    assert client.post(path, json=body).status_code == 401
