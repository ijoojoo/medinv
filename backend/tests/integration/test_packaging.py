from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_retail import retail as retail
from test_sales import sale as sale

from medinv.modules.catalog.api.packaging import PackagingCreate
from medinv.modules.catalog.api.schemas import Review
from medinv.modules.catalog.application.packaging import PackagingService
from medinv.modules.procurement.api.schemas import InspectionCreate, ReceiptCreate, Transition
from medinv.modules.returns.api.schemas import ReturnCreate, ReturnReceive, ReturnReview
from medinv.modules.returns.application import custody
from medinv.modules.returns.application import posting as return_posting
from medinv.modules.returns.application.service import ReturnService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def service(f):
    return PackagingService(f.world.uow)


def create(f, n="3", d="1", name="箱", body=None, key=None, ctx=None):
    svc = service(f)
    return svc.create(
        ctx or f.maker,
        f.sku,
        body
        or PackagingCreate(
            expected_sku_version=svc.listing(f.maker, f.sku)["sku_version"],
            unit_name=name,
            numerator=n,
            denominator=d,
            reason="核对实物包装",
        ),
        key or str(uuid4()),
    )


def review(f, row, ctx=None, decision="APPROVE", key=None):
    return service(f).review(
        ctx or f.reviewer,
        f.sku,
        UUID(row["id"]),
        Review(
            expected_version=row["version"],
            decision=decision,
            evidence_ref="PACK-TEST",
            reason="独立复核包装比例",
        ),
        key or str(uuid4()),
    )


def approved(f, n="3", d="1"):
    row = create(f, n, d)
    return review(f, row)


def line(f, p, qty="1", price="1"):
    return {
        "sku_id": f.sku,
        "packaging_id": p["id"],
        "qty": qty,
        "unit_price": price,
        "tax_rate": "0.13",
    }


def receive_and_post(f, oid, qty):
    doc = f.service.detail(f.maker, oid)
    received = f.service.receive(
        f.maker,
        oid,
        ReceiptCreate(
            expected_version=doc["order"]["version"],
            shipment_ref="BOX-DELIVERY",
            transport_note="包装按基本单位清点",
            lines=[
                {
                    "order_line_id": doc["lines"][0]["id"],
                    "location_id": f.world.location,
                    "qty": qty,
                    "lot_no": "BOX-BATCH",
                    "mfg_date": f.day - timedelta(days=1),
                    "expiry_date": f.day + timedelta(days=365),
                }
            ],
        ),
        str(uuid4()),
    )
    rec = next(r for r in f.service.detail(f.maker, oid)["receipts"] if r["id"] == received["id"])
    inspected = f.service.inspect(
        f.reviewer,
        UUID(received["id"]),
        InspectionCreate(
            expected_version="1",
            note="核验基本单位数量",
            lines=[
                {
                    "receipt_line_id": rec["lines"][0]["id"],
                    "qualified_qty": qty,
                    "rejected_qty": "0",
                }
            ],
        ),
        str(uuid4()),
    )
    first = f.post(inspected)
    assert f.post(inspected) == first
    return first


def test_packaging_review_replacement_and_independent_approval(flow):
    f = flow
    draft = create(f, "6", "2")
    with pytest.raises(BusinessError) as exc:
        review(f, draft, ctx=f.maker)
    assert exc.value.code == "SEPARATION_REQUIRED"
    with pytest.raises(BusinessError):
        f.order(lines=[line(f, draft)])
    first = review(f, draft)
    assert service(f).listing(f.maker, f.sku, approved_only=True)["items"][0]["numerator"] == "3"
    second = approved(f, "6")
    rows = service(f).listing(f.maker, f.sku)["items"]
    assert [r["state"] for r in rows] == ["APPROVED", "RETIRED"]
    assert rows[0]["revision"] == 2
    with pytest.raises(BusinessError) as exc:
        f.order(lines=[line(f, first)])
    assert exc.value.code == "PACKAGING_UNAVAILABLE"
    with pytest.raises(BusinessError):
        review(f, rows[1])
    review(f, second, decision="SUSPEND")
    assert service(f).listing(f.maker, f.sku, approved_only=True)["items"] == []


def test_purchase_partial_posting_retains_original_conversion_and_total(flow):
    f = flow
    p = approved(f)
    oid = f.order(lines=[line(f, p)])
    approved(f, "6")  # This must not reprice or double the old order's stock.
    doc = f.service.detail(f.maker, oid)
    assert Decimal(doc["lines"][0]["qty"]) == 3
    assert Decimal(doc["lines"][0]["trade_qty"]) == 1
    for qty in ("1", "1", "1"):
        receive_and_post(f, oid, qty)
    assert (
        f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=f.sku) == 3
    )
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 1
    assert f.world.scalar("SELECT sum(net_amount) FROM med.payable_obligation") == Decimal("0.88")
    assert f.world.scalar(
        "SELECT sum(amount) FROM med.cost_balance WHERE sku_id=:s", s=f.sku
    ) == Decimal("0.88")
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.posting_line WHERE package_snapshot->>'packaging_id'=:p",
            p=p["id"],
        )
        == 3
    )
    assert f.world.scalar("SELECT max(gross_amount) FROM med.posting_line") == Decimal("0.34")


def test_wholesale_package_reserves_base_quantity_and_preserves_price_on_partial_shipments(sale):
    s, f = sale, sale.f
    p = approved(f)
    oid = s.order(lines=[line(f, p)])
    review(f, p, decision="SUSPEND")
    for _ in range(3):
        s.post(s.ready("1", oid))
    assert s.scalar("on_hand") == 7
    assert s.scalar("reserved") == 0
    assert s.detail(oid)["order"]["gross_amount"] == "1.00"
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.receivable_obligation") == 1
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.posting_line WHERE package_snapshot->>'packaging_id'=:p",
            p=p["id"],
        )
        == 3
    )


def test_retail_package_reserves_and_sells_base_units_at_package_price(retail):
    r, f = retail, retail.f
    p = approved(f, "6")
    result = r.create(lines=[line(f, p, "0.5", "12")])
    assert result["gross_amount"] == "6.00"
    assert (
        f.world.scalar("SELECT sum(reserved) FROM med.stock_balance WHERE sku_id=:s", s=f.sku) == 3
    )
    review(f, p, decision="SUSPEND")
    r.pay(result["id"])
    posted = r.complete(result["id"])
    row = r.detail(result["id"])["lines"][0]
    assert Decimal(row["trade_qty"]) == Decimal("0.5")
    assert Decimal(row["qty"]) == 3
    assert row["package_snapshot"]["numerator"] == 6
    assert (
        f.world.scalar(
            "SELECT sum(gross_amount) FROM med.posting_line WHERE posting_id=:id",
            id=UUID(posted["posting_id"]),
        )
        == 6
    )


@pytest.mark.parametrize("scope", ["tenant", "sku", "permission"])
def test_packaging_scope_and_permission(flow, scope):
    f = flow
    p = approved(f)
    ctx = replace(f.maker, tenant_id=uuid4()) if scope == "tenant" else f.maker
    if scope == "permission":
        ctx = replace(ctx, permissions=ROLE_PERMISSIONS["VIEWER"])
    if scope == "sku":
        with pytest.raises(BusinessError):
            service(f).review(
                ctx,
                f.world.sku,
                UUID(p["id"]),
                Review(
                    expected_version=p["version"],
                    decision="SUSPEND",
                    evidence_ref="test",
                    reason="错误归属",
                ),
                str(uuid4()),
            )
    else:
        with pytest.raises(BusinessError):
            create(f, name="包", ctx=ctx)


def test_packaging_optimistic_concurrency_and_retry(flow):
    f = flow
    body = PackagingCreate(
        expected_sku_version=service(f).listing(f.maker, f.sku)["sku_version"],
        unit_name="箱",
        numerator="12",
        denominator="1",
        reason="包装核验",
    )
    key = str(uuid4())
    first = create(f, body=body, key=key)
    assert create(f, body=body, key=key) == first
    with pytest.raises(BusinessError) as exc:
        create(f, body=body, key=str(uuid4()))
    assert exc.value.code == "VERSION_CONFLICT"
    next_body = body.model_copy(
        update={
            "unit_name": "包",
            "expected_sku_version": service(f).listing(f.maker, f.sku)["sku_version"],
        }
    )

    def attempt(_):
        try:
            return create(f, body=next_body)
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "VERSION_CONFLICT" in results


@pytest.mark.parametrize("operation", ["create", "review"])
def test_revocation_rolls_back_package_sku_version_and_audit(flow, operation):
    f = flow
    draft = create(f)
    before = service(f).listing(f.maker, f.sku)
    count = f.world.scalar("SELECT count(*) FROM med.audit_event")

    def fail():
        raise BusinessError("REVOKED", "提交时撤权", 403)

    token = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            create(f, name="包") if operation == "create" else review(f, draft)
    finally:
        before_business_commit.reset(token)
    assert service(f).listing(f.maker, f.sku) == before
    assert f.world.scalar("SELECT count(*) FROM med.audit_event") == count


def test_database_protects_package_definition_and_order_snapshot(flow):
    f = flow
    p = approved(f)
    oid = f.order(lines=[line(f, p)])
    for sql, value in [
        ("UPDATE med.packaging SET numerator=5 WHERE id=:id", UUID(p["id"])),
        ("DELETE FROM med.packaging WHERE id=:id", UUID(p["id"])),
        ("UPDATE med.purchase_order_line SET qty=4 WHERE order_id=:id", oid),
        ("UPDATE med.purchase_order_line SET package_snapshot=NULL WHERE order_id=:id", oid),
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql, id=value)


def test_invalid_base_precision_rejects_whole_order(flow):
    f = flow
    p = approved(f, "3", "2")
    with pytest.raises(BusinessError) as exc:
        f.order(lines=[line(f, p)])
    assert exc.value.code == "INVALID_QUANTITY"
    assert f.world.scalar("SELECT count(*) FROM med.purchase_order") == 1
    oid = f.order(lines=[line(f, p, "2")])
    assert Decimal(f.service.detail(f.maker, oid)["lines"][0]["qty"]) == 3


@pytest.mark.parametrize("kind", ["PURCHASE", "RETAIL"])
def test_partial_returns_use_base_quantity_and_original_package_money(retail, kind):
    r, f = retail, retail.f
    p = approved(f)
    if kind == "PURCHASE":
        oid = f.order(lines=[line(f, p)])
        original = receive_and_post(f, oid, "3")
    else:
        sid = r.create(lines=[line(f, p)])["id"]
        r.pay(sid)
        original = r.complete(sid)
    approved(f, "12")
    svc = ReturnService(f.world.uow)
    source_id = UUID(original["posting_id"])
    source_line = svc.source_detail(f.maker, source_id)["lines"][0]
    totals = []
    for _ in range(3):
        rid = UUID(
            svc.create(
                f.maker,
                ReturnCreate(
                    client_operation_id=uuid4(),
                    source_posting_id=source_id,
                    reason="拆零退回基本单位",
                    lines=[{"source_posting_line_id": source_line["id"], "qty": "1"}],
                ),
                str(uuid4()),
            )["id"]
        )
        checks = [
            {
                "source_posting_line_id": source_line["id"],
                "qty": "1",
                "lot_no": source_line["lot_no"],
            }
        ]
        if kind == "RETAIL":
            custody.receive(
                svc,
                f.maker,
                rid,
                ReturnReceive(
                    expected_version="1",
                    reason="已收货",
                    zone_label="隔离区",
                    confirmed=True,
                    lines=checks,
                ),
                str(uuid4()),
            )
        doc = svc.detail(f.maker, rid)["document"]
        totals.append(Decimal(doc["gross_amount"]))
        svc.review(
            f.reviewer,
            rid,
            ReturnReview(
                expected_version=doc["version"], reason="实物核查", decision="APPROVE", lines=checks
            ),
            str(uuid4()),
        )
        doc = svc.detail(f.maker, rid)["document"]
        return_posting.post(
            svc,
            f.reviewer,
            rid,
            Transition(expected_version=doc["version"], reason="按原成本过账"),
            str(uuid4()),
        )
    assert totals == [Decimal("0.33"), Decimal("0.34"), Decimal("0.33")]
    assert (
        f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=f.sku) == 10
    )
    assert f.world.scalar(
        "SELECT sum(amount) FROM med.cost_balance WHERE sku_id=:s", s=f.sku
    ) == Decimal("115.04")
