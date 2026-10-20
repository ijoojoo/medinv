from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_api import validate_response
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.catalog.api.schemas import PartyCreate
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.sales.api.schemas import CreditSet
from medinv.modules.settlement.api.schemas import (
    AccountCreate,
    AllocateRequest,
    CashCreate,
    ReverseRequest,
)
from medinv.modules.settlement.application import allocation
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def finance(sale):
    s = sale
    s.post(s.ready("3"))
    service = SettlementService(s.f.world.uow)
    maker = s.f.maker
    account = service.create_account(
        maker,
        AccountCreate(entity_id=s.f.world.entity, code="BANK1", name="测试银行账簿", method="BANK"),
        str(uuid4()),
    )["id"]

    def items(kind="AR"):
        return service.items(maker, s.f.world.entity, kind, 0)["items"]

    def cash(amount="60", direction="RECEIPT", op=None, ref=None, party=None):
        return service.create_cash(
            maker,
            CashCreate(
                client_operation_id=op or uuid4(),
                entity_id=s.f.world.entity,
                party_id=party or s.customer,
                account_id=account,
                direction=direction,
                amount=amount,
                occurred_on=s.f.day,
                reference=ref or str(uuid4()),
                note="测试已经发生的款项",
                confirmed=True,
            ),
            str(uuid4()),
        )

    def detail(cid):
        return service.detail(maker, UUID(str(cid)))

    def body(cid, amount="60", kind="AR", op=None, lines=None):
        return AllocateRequest(
            client_operation_id=op or uuid4(),
            cash_document_id=cid,
            expected_cash_version=detail(cid)["cash"]["version"],
            allocations=lines
            or [
                {
                    "settlement_item_id": items(kind)[0]["id"],
                    "expected_item_version": items(kind)[0]["version"],
                    "amount": amount,
                }
            ],
        )

    def allocate(cid, amount="60", kind="AR", request=None):
        return allocation.allocate(service, maker, request or body(cid, amount, kind), str(uuid4()))

    def reverse(aid, request=None):
        return allocation.reverse(
            service,
            maker,
            UUID(aid),
            request or ReverseRequest(client_operation_id=uuid4(), reason="撤销错配"),
            str(uuid4()),
        )

    return SimpleNamespace(**locals())


def test_receipt_partial_full_and_reversal_update_credit_but_not_stock(finance):
    f = finance
    cid = f.cash("100")["id"]
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["receivable"] == "60.00"
    first = f.allocate(cid, "20")
    source = f.s.f.world.scalar("SELECT number FROM med.shipment WHERE state='POSTED'")
    assert f.items()[0]["source_number"] == source
    assert f.detail(cid)["allocations"][0]["source_number"] == source
    assert f.items()[0]["outstanding_amount"] == "40.00"
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["receivable"] == "40.00"
    f.allocate(cid, "40")
    assert f.items()[0]["outstanding_amount"] == "0.00"
    assert f.detail(cid)["cash"]["remaining_amount"] == "40.00"
    f.reverse(first["allocation_ids"][0])
    assert f.items()[0]["outstanding_amount"] == "20.00"
    assert f.items()[0]["version"] == "4"
    assert f.s.scalar("on_hand") == 7
    assert f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("80.528")
    assert f.s.f.world.scalar("SELECT gross_amount FROM med.receivable_obligation") == 60


def test_supplier_payment_does_not_offset_customer_ar(finance):
    f = finance
    cid = f.cash("130", "PAYMENT")["id"]
    f.allocate(cid, "130", "AP")
    source = f.s.f.world.scalar("SELECT number FROM med.receipt")
    assert f.items("AP")[0]["source_number"] == source
    assert f.detail(cid)["allocations"][0]["source_number"] == source
    assert f.items("AP")[0]["outstanding_amount"] == "0.00"
    assert f.items()[0]["outstanding_amount"] == "60.00"
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["receivable"] == "60.00"


def test_multiple_obligations_are_allocated_in_one_transaction(finance):
    f = finance
    f.s.post(f.s.ready("2"))
    cid = f.cash("100")["id"]
    req = f.body(
        cid,
        lines=[
            {
                "settlement_item_id": r["id"],
                "expected_item_version": r["version"],
                "amount": r["outstanding_amount"],
            }
            for r in f.items()
        ],
    )
    f.allocate(cid, request=req)
    assert all(r["outstanding_amount"] == "0.00" for r in f.items())
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["receivable"] == "0.00"


def test_one_invalid_line_rolls_back_all_allocations(finance):
    f = finance
    f.s.post(f.s.ready("2"))
    cid = f.cash("200")["id"]
    rows = f.items()
    req = f.body(
        cid,
        lines=[
            {
                "settlement_item_id": r["id"],
                "expected_item_version": r["version"],
                "amount": "100" if i else "10",
            }
            for i, r in enumerate(rows)
        ],
    )
    with pytest.raises(BusinessError) as err:
        f.allocate(cid, request=req)
    assert err.value.code == "OBLIGATION_EXCEEDED"
    assert f.s.f.world.scalar("SELECT count(*) FROM med.settlement_allocation") == 0
    assert f.detail(cid)["cash"]["allocated_amount"] == "0.00"


def test_concurrent_same_operation_posts_once_even_with_new_keys(finance):
    f = finance
    cid = f.cash()["id"]
    req = f.body(cid)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: f.allocate(cid, request=req), range(6)))
    assert all(r == results[0] for r in results)
    assert f.s.f.world.scalar("SELECT count(*) FROM med.settlement_allocation") == 1
    assert f.items()[0]["outstanding_amount"] == "0.00"


def test_two_cash_documents_cannot_over_settle_one_obligation(finance):
    f = finance
    requests = [f.body(f.cash()["id"]) for _ in range(2)]

    def attempt(req):
        try:
            f.allocate(req.cash_document_id, request=req)
            return "OK"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, requests))
    assert results.count("OK") == 1
    assert f.s.f.world.scalar("SELECT sum(amount) FROM med.settlement_allocation") == 60


def test_cash_reference_and_permanent_operation_prevent_duplicate_facts(finance):
    f = finance
    op, reference = uuid4(), str(uuid4())
    results = [f.cash(op=op, ref=reference) for _ in range(2)]
    assert results[0] == results[1]
    with pytest.raises(BusinessError) as err:
        f.cash(ref=reference)
    assert err.value.code == "CASH_REFERENCE_EXISTS"
    with pytest.raises(BusinessError) as err:
        f.cash("59", op=op, ref=reference)
    assert err.value.code == "OPERATION_CONFLICT"


def test_reverse_is_once_only_and_void_requires_no_active_allocation(finance):
    f = finance
    cid = f.cash()["id"]
    aid = f.allocate(cid)["allocation_ids"][0]
    with pytest.raises(BusinessError) as err:
        f.service.void(
            f.maker, UUID(cid), Transition(expected_version="2", reason="错录"), str(uuid4())
        )
    assert err.value.code == "CASH_ALLOCATED"
    req = ReverseRequest(client_operation_id=uuid4(), reason="撤销")
    assert f.reverse(aid, req) == f.reverse(aid, req)
    with pytest.raises(BusinessError) as err:
        f.reverse(aid)
    assert err.value.code == "ALLOCATION_REVERSED"
    f.service.void(
        f.maker, UUID(cid), Transition(expected_version="3", reason="错录作废"), str(uuid4())
    )
    with pytest.raises(BusinessError) as err:
        f.allocate(cid)
    assert err.value.code == "CASH_VOIDED"
    assert f.detail(cid)["void"]["reason"] == "错录作废"
    assert f.items()[0]["outstanding_amount"] == "60.00"


def test_settled_overdue_debt_unblocks_sales_and_reversal_blocks_again(finance, monkeypatch):
    f = finance
    real_now = f.s.service.now
    monkeypatch.setattr(f.s.service, "now", lambda repo: real_now(repo) + timedelta(days=31))
    with pytest.raises(BusinessError) as err:
        f.s.order("1")
    assert err.value.code == "CREDIT_OVERDUE"
    cid = f.cash()["id"]
    aid = f.allocate(cid)["allocation_ids"][0]
    f.s.order("1")
    f.reverse(aid)
    with pytest.raises(BusinessError) as err:
        f.s.order("1")
    assert err.value.code == "CREDIT_OVERDUE"


def test_commit_probe_failure_rolls_back_cash_allocations_and_credit(finance):
    f = finance
    cid = f.cash()["id"]

    def fail():
        raise RuntimeError("lost authorization")

    token = before_business_commit.set(fail)
    try:
        with pytest.raises(RuntimeError):
            f.allocate(cid)
    finally:
        before_business_commit.reset(token)
    assert f.detail(cid)["cash"]["allocated_amount"] == "0.00"
    assert f.items()[0]["outstanding_amount"] == "60.00"
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["receivable"] == "60.00"


def test_immutable_facts_and_invoker_view_enforce_rls(finance):
    f = finance
    cid = f.cash()["id"]
    f.allocate(cid)
    for sql in [
        "UPDATE med.cash_document SET amount=100",
        "UPDATE med.settlement_allocation SET amount=1",
        "DELETE FROM med.settlement_allocation",
        "UPDATE med.receivable_obligation SET gross_amount=1",
    ]:
        with pytest.raises(DBAPIError):
            f.s.f.world.execute(sql)
    other = replace(f.maker, tenant_id=uuid4())
    with f.service.uow.open(other, readonly=True) as session:
        assert session.execute(text("SELECT count(*) FROM med.settlement_item")).scalar_one() == 0
    viewer = replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    with pytest.raises(BusinessError):
        allocation.allocate(f.service, viewer, f.body(cid), str(uuid4()))
    with pytest.raises(BusinessError):
        f.service.detail(replace(f.maker, entity_ids=frozenset()), UUID(cid))


def test_cash_and_obligation_directions_and_amount_limits(finance):
    f = finance
    cid = f.cash("20")["id"]
    with pytest.raises(BusinessError) as err:
        f.allocate(cid, "21")
    assert err.value.code == "CASH_EXCEEDED"
    with pytest.raises(BusinessError):
        f.allocate(cid, "20", "AP")
    assert f.s.f.world.scalar("SELECT count(*) FROM med.settlement_allocation") == 0


def test_finance_api_matches_original_allocation_contract(finance, api):
    f = finance
    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                """UPDATE control.membership SET permissions=:perms
                WHERE tenant_id=:t AND principal_id=:p"""
            ),
            {
                "perms": list(ROLE_PERMISSIONS["OWNER"]),
                "t": f.maker.tenant_id,
                "p": f.maker.principal_id,
            },
        )
    cid = f.cash()["id"]
    response = client.post(
        "/api/v1/settlements/allocations", json=f.body(cid).model_dump(mode="json")
    )
    assert response.status_code == 200, response.json()
    validate_response(response, "AllocationResultResponse")


def test_wrong_counterparty_cannot_allocate_another_customers_receivable(finance):
    f = finance
    other = f.s.f.catalog.create_party(
        f.maker,
        PartyCreate(
            entity_id=f.s.f.world.entity,
            code="OTHER",
            name="另一往来单位",
            credit_code="TEST",
            kind="CUSTOMER",
            license_no="TEST",
            valid_from=f.s.f.day,
            valid_until=f.s.f.day + timedelta(days=200),
            categories=["DEVICE"],
        ),
        str(uuid4()),
    )
    cid = f.cash(party=other["id"])["id"]
    with pytest.raises(BusinessError) as err:
        f.allocate(cid)
    assert err.value.code == "SETTLEMENT_SCOPE_MISMATCH"
    assert f.detail(cid)["cash"]["allocated_amount"] == "0.00"


def test_reversal_restores_risk_even_after_credit_limit_is_reduced(finance):
    f = finance
    cid = f.cash()["id"]
    aid = f.allocate(cid)["allocation_ids"][0]
    credit = f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)
    f.s.service.set_credit(
        f.maker,
        f.s.f.world.entity,
        f.s.customer,
        CreditSet(
            expected_version=credit["version"],
            credit_limit="140",
            valid_until=f.s.f.day,
            reason="降低到当前未发敞口",
        ),
        str(uuid4()),
    )
    f.reverse(aid)
    assert f.s.service.credit(f.maker, f.s.f.world.entity, f.s.customer)["available"] == "-60.00"
    assert f.items()[0]["outstanding_amount"] == "60.00"
    with pytest.raises(BusinessError) as err:
        f.s.order("1")
    assert err.value.code == "CREDIT_LIMIT_EXCEEDED"
