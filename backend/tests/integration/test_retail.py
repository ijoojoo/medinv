from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_procurement import flow as flow

from medinv.modules.catalog.api.schemas import ProductCreate, Review
from medinv.modules.inventory.api.schemas import ReleaseRequest
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import (
    RetailCashConfirmation,
    RetailComplete,
    RetailCreate,
    RetailPayment,
)
from medinv.modules.retail.application import payments, posting
from medinv.modules.retail.application.service import RetailService
from medinv.modules.settlement.api.schemas import AccountCreate
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.uow import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def retail(flow):
    f = flow
    f.post(f.inspect(f.receive()["id"]))
    service = RetailService(f.world.uow)
    account = service.finance.create_account(
        f.maker,
        AccountCreate(entity_id=f.world.entity, code="CASH", name="收银现金", method="CASH"),
        str(uuid4()),
    )

    def create(qty="3", price="20", tax="0.13", lines=None, body=None):
        return service.create(
            f.maker,
            body
            or RetailCreate(
                client_operation_id=uuid4(),
                entity_id=f.world.entity,
                warehouse_id=f.world.warehouse,
                lines=lines
                or [{"sku_id": f.sku, "qty": qty, "unit_price": price, "tax_rate": tax}],
            ),
            str(uuid4()),
        )

    def detail(sid):
        return service.detail(f.maker, UUID(str(sid)))

    def pay(sid, body=None):
        return payments.receive(
            service,
            f.maker,
            UUID(str(sid)),
            body
            or RetailPayment(
                client_operation_id=uuid4(),
                expected_version=detail(sid)["sale"]["version"],
                account_id=account["id"],
                tendered="100",
                confirmed=True,
                reason="实际现金已核对",
            ),
            str(uuid4()),
        )

    def check(sid):
        doc = detail(sid)
        return RetailComplete(
            expected_version=doc["sale"]["version"],
            reason="实物已交付核对",
            lines=[
                {"allocation_id": r["id"], "qty": r["allocated_qty"], "lot_no": r["lot_no"]}
                for r in doc["allocations"]
            ],
        )

    def complete(sid, body=None):
        return posting.complete(service, f.maker, UUID(str(sid)), body or check(sid), str(uuid4()))

    def refund(sid, ctx=None):
        return payments.refund_pending(
            service,
            ctx or f.maker,
            UUID(str(sid)),
            RetailCashConfirmation(
                client_operation_id=uuid4(),
                expected_version="2",
                confirmed=True,
                reason="已将实收现金退还顾客",
            ),
            str(uuid4()),
        )

    return SimpleNamespace(**locals())


def test_cash_sale_posts_stock_cost_and_snapshots_without_customer_or_receivable(retail):
    r = retail
    sale = r.create()
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3
    assert r.pay(sale["id"])["change_amount"] == "40.00"
    body = r.check(sale["id"])
    result = r.complete(sale["id"], body)
    assert result["cost_amount"] == "34.51200000"
    assert r.complete(sale["id"], body) == result
    doc = r.detail(sale["id"])
    assert doc["sale"]["state"] == "COMPLETED"
    assert doc["reservation"]["state"] == "CONSUMED"
    assert doc["reservation"]["protected_until"] is None
    assert len(doc["payments"]) == 1
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 7
    assert r.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("80.528")
    assert r.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert r.f.world.scalar("SELECT count(*) FROM med.party") == 1  # Original supplier only.
    assert (
        r.f.world.scalar(
            """SELECT sum(gross_amount) FROM med.posting_line
        WHERE posting_id=:id""",
            id=UUID(result["posting_id"]),
        )
        == 60
    )
    with pytest.raises(BusinessError):
        r.refund(sale["id"])


def test_paid_failure_survives_and_can_retry_after_quality_is_restored(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)
    r.f.world.execute("UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s", s=r.f.sku)
    with pytest.raises(BusinessError) as err:
        r.complete(sid)
    assert err.value.code == "RETAIL_LOT_INELIGIBLE"
    assert r.detail(sid)["sale"]["state"] == "PAID_PENDING_POST"
    assert len(r.detail(sid)["payments"]) == 1
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3
    # Paid stock remains protected even after the unpaid reservation TTL elapsed.
    r.f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 day'")
    r.f.world.execute("UPDATE med.lot SET quality_blocked=false WHERE sku_id=:s", s=r.f.sku)
    assert r.complete(sid)["state"] == "COMPLETED"


def test_paid_cash_refund_releases_reservation_without_stock_or_cost_posting(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)
    ctx = replace(r.f.maker, permissions=ROLE_PERMISSIONS["FINANCE"])
    result = r.refund(sid, ctx)
    assert result["amount"] == "60.00"
    doc = r.detail(sid)
    assert doc["sale"]["state"] == "REFUNDED"
    assert doc["reservation"]["state"] == "RELEASED"
    assert [p["direction"] for p in doc["payments"]] == ["RECEIPT", "REFUND"]
    assert doc["payments"][1]["account_id"] == r.account["id"]
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10
    assert r.f.world.scalar("SELECT count(*) FROM med.posting_batch") == 1  # Purchase only.
    with pytest.raises(BusinessError):
        r.complete(sid)


def test_unpaid_expiry_cannot_charge_but_cancel_frees_stock(retail):
    r = retail
    sid = r.create()["id"]
    r.f.world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 day'")
    with pytest.raises(BusinessError) as err:
        r.pay(sid)
    assert err.value.code == "RESERVATION_EXPIRED"
    assert r.detail(sid)["payments"] == []
    r.service.cancel(
        r.f.maker, UUID(sid), Transition(expected_version="1", reason="超时重建"), str(uuid4())
    )
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
    assert r.detail(sid)["sale"]["state"] == "CANCELLED"


def test_public_release_and_direct_unpaid_post_cannot_bypass_retail_state(retail):
    r = retail
    sid = r.create()["id"]
    doc = r.detail(sid)
    with pytest.raises(BusinessError) as err:
        r.complete(sid)
    assert err.value.code == "RETAIL_NOT_PAID"
    with pytest.raises(BusinessError) as err:
        r.service.inventory.release(
            r.f.maker,
            UUID(doc["reservation"]["id"]),
            ReleaseRequest(expected_version="1", reason="禁止绕过零售"),
            str(uuid4()),
        )
    assert err.value.code == "RETAIL_WORKFLOW_REQUIRED"
    r.pay(sid)
    with pytest.raises(BusinessError):
        r.service.cancel(
            r.f.maker, UUID(sid), Transition(expected_version="2", reason="禁止取消"), str(uuid4())
        )
    assert r.detail(sid)["sale"]["state"] == "PAID_PENDING_POST"


def test_payment_operation_is_permanent_and_never_receives_cash_twice(retail):
    r = retail
    sid = r.create()["id"]
    body = RetailPayment(
        client_operation_id=uuid4(),
        expected_version="1",
        account_id=r.account["id"],
        tendered="100",
        confirmed=True,
        reason="实收确认",
    )
    first = r.pay(sid, body)
    assert r.pay(sid, body) == first  # New HTTP key; same durable operation.
    with pytest.raises(BusinessError):
        r.pay(sid)
    with pytest.raises(BusinessError) as err:
        r.pay(sid, body.model_copy(update={"tendered": "200.00"}))
    assert err.value.code == "OPERATION_CONFLICT"
    assert len(r.detail(sid)["payments"]) == 1


def test_cash_shortage_and_wrong_batch_do_not_create_partial_facts(retail):
    r = retail
    sid = r.create()["id"]
    body = RetailPayment(
        client_operation_id=uuid4(),
        expected_version="1",
        account_id=r.account["id"],
        tendered="59.99",
        confirmed=True,
        reason="核对金额",
    )
    with pytest.raises(BusinessError) as err:
        r.pay(sid, body)
    assert err.value.code == "CASH_SHORT"
    assert not r.detail(sid)["payments"]
    r.pay(sid)
    check = r.check(sid)
    check.lines[0].lot_no = "WRONG"
    with pytest.raises(BusinessError) as err:
        r.complete(sid, check)
    assert err.value.code == "RETAIL_CHECK_MISMATCH"
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10


def test_complete_and_refund_race_has_one_terminal_result(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)

    def execute(action):
        try:
            return action(sid)["state"]
        except BusinessError:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [r.complete, r.refund]))
    assert results.count("CONFLICT") == 1
    doc = r.detail(sid)
    assert doc["sale"]["state"] in {"COMPLETED", "REFUNDED"}
    assert len(doc["payments"]) == (1 if doc["sale"]["state"] == "COMPLETED" else 2)
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0


def test_commit_failure_rolls_back_posting_but_preserves_prior_payment(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)

    def revoke():
        raise BusinessError("SESSION_REVOKED", "提交前撤权", 401)

    with before_business_commit.set(revoke), pytest.raises(BusinessError):
        r.complete(sid)
    assert r.detail(sid)["sale"]["state"] == "PAID_PENDING_POST"
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10
    assert r.f.world.scalar("SELECT count(*) FROM med.posting_batch") == 1
    assert r.complete(sid)["state"] == "COMPLETED"


def test_retail_facts_are_immutable_and_tenant_entity_role_scoped(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)
    for sql in (
        "UPDATE med.retail_sale_line SET qty=1",
        "DELETE FROM med.retail_payment",
        "UPDATE med.retail_sale SET gross_amount=1",
    ):
        with pytest.raises(DBAPIError):
            r.f.world.execute(sql)
    for ctx in (replace(r.f.maker, tenant_id=uuid4()), replace(r.f.maker, entity_ids=frozenset())):
        with pytest.raises(BusinessError):
            r.service.detail(ctx, UUID(sid))
    viewer = replace(r.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert r.service.detail(viewer, UUID(sid))["sale"]["state"] == "PAID_PENDING_POST"
    with pytest.raises(BusinessError):
        posting.complete(r.service, viewer, UUID(sid), r.check(sid), str(uuid4()))


@pytest.mark.parametrize("classification", ["UNKNOWN", "RX", "OTC"])
def test_drug_classification_requires_reviewed_otc(retail, classification):
    r = retail
    f = r.f
    product = f.catalog.create_product(
        f.maker,
        ProductCreate(
            code="D1",
            name="药品分类测试",
            category="DRUG",
            drug_sale_class=classification,
            manufacturer_name="测试厂家",
            specification="一盒",
            approval_number="TEST-ONLY",
            valid_from=f.day,
            valid_until=f.day,
            sku_code="D-SKU",
            base_unit="盒",
            qty_scale=0,
        ),
        str(uuid4()),
    )
    f.catalog.review(
        f.reviewer,
        "product",
        UUID(product["id"]),
        Review(
            expected_version="1", decision="APPROVE", evidence_ref="TEST", reason="独立核定分类"
        ),
        str(uuid4()),
    )
    f.world.add_balance(sku=UUID(product["sku_id"]))
    f.world.execute(
        """INSERT INTO med.stock_gate
        (tenant_id,id,created_by,updated_by,entity_id,sku_id)
        VALUES(:t,:id,:a,:a,:e,:s)""",
        t=f.maker.tenant_id,
        id=uuid4(),
        a=f.maker.principal_id,
        e=f.world.entity,
        s=UUID(product["sku_id"]),
    )
    body = RetailCreate(
        client_operation_id=uuid4(),
        entity_id=f.world.entity,
        warehouse_id=f.world.warehouse,
        lines=[{"sku_id": product["sku_id"], "qty": "1", "unit_price": "1", "tax_rate": "0.13"}],
    )
    catalog = r.service.catalog(f.maker, f.world.entity, "药品分类", 0)
    if classification == "OTC":
        assert r.create(body=body)["state"] == "RESERVED"
        assert len(catalog["items"]) == 1
    else:
        with pytest.raises(BusinessError) as err:
            r.create(body=body)
        assert err.value.code == "RETAIL_RX_NOT_ENABLED"
        assert catalog["items"] == []


def test_concurrent_carts_cannot_oversell_and_failed_cart_leaves_no_demand(retail):
    r = retail

    def run(_):
        try:
            return r.create(qty="7")["state"]
        except BusinessError:
            return "SHORT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["RESERVED", "SHORT"]
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 7
    assert r.f.world.scalar("SELECT count(*) FROM med.retail_sale") == 1
    assert r.f.world.scalar("SELECT count(*) FROM med.inventory_demand") == 1


def test_repeated_sku_lines_share_one_cost_pool_without_losing_amounts(retail):
    r = retail
    sid = r.create(
        lines=[
            {"sku_id": r.f.sku, "qty": q, "unit_price": "0.01", "tax_rate": "0.13"}
            for q in ("3", "7")
        ]
    )["id"]
    r.pay(sid)
    result = r.complete(sid)
    assert result["cost_amount"] == "115.04000000"
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 0
    assert r.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == 0
    pid = UUID(result["posting_id"])
    assert r.f.world.scalar(
        "SELECT sum(gross_amount) FROM med.posting_line WHERE posting_id=:p", p=pid
    ) == Decimal("0.10")
    assert r.f.world.scalar(
        "SELECT sum(net_amount) FROM med.posting_line WHERE posting_id=:p", p=pid
    ) == Decimal("0.09")


def test_multi_lot_sale_splits_original_money_exactly(flow):
    f = flow
    for qty, lot in [("3", "EARLY"), ("7", "LATE")]:
        f.post(f.inspect(f.receive(qty, lot)["id"], qty))
    service = RetailService(f.world.uow)
    account = service.finance.create_account(
        f.maker,
        AccountCreate(entity_id=f.world.entity, code="POS", name="现金", method="CASH"),
        str(uuid4()),
    )
    created = service.create(
        f.maker,
        RetailCreate(
            client_operation_id=uuid4(),
            entity_id=f.world.entity,
            warehouse_id=f.world.warehouse,
            lines=[{"sku_id": f.sku, "qty": "9", "unit_price": "0.01", "tax_rate": "0.13"}],
        ),
        str(uuid4()),
    )
    sid = UUID(created["id"])
    payments.receive(
        service,
        f.maker,
        sid,
        RetailPayment(
            client_operation_id=uuid4(),
            expected_version="1",
            account_id=account["id"],
            tendered="1",
            confirmed=True,
            reason="测试收款",
        ),
        str(uuid4()),
    )
    doc = service.detail(f.maker, sid)
    assert len(doc["allocations"]) == 2
    result = posting.complete(
        service,
        f.maker,
        sid,
        RetailComplete(
            expected_version="2",
            reason="核对",
            lines=[
                {"allocation_id": a["id"], "qty": a["allocated_qty"], "lot_no": a["lot_no"]}
                for a in doc["allocations"]
            ],
        ),
        str(uuid4()),
    )
    pid = UUID(result["posting_id"])
    assert f.world.scalar(
        "SELECT sum(gross_amount) FROM med.posting_line WHERE posting_id=:p", p=pid
    ) == Decimal("0.09")
    assert f.world.scalar(
        "SELECT sum(net_amount) FROM med.posting_line WHERE posting_id=:p", p=pid
    ) == Decimal("0.08")


def test_http_retail_write_requires_role_and_openapi_matches_implementation(api, retail):
    client, _ = api
    f = retail.f
    response = client.post(
        "/api/v1/retail/sales",
        json={
            "client_operation_id": str(uuid4()),
            "entity_id": str(f.world.entity),
            "warehouse_id": str(f.world.warehouse),
            "lines": [{"sku_id": str(f.sku), "qty": "1", "unit_price": "1", "tax_rate": "0"}],
        },
    )
    assert response.status_code == 403
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/retail/sales/{id}/complete"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RetailComplete"
    )
