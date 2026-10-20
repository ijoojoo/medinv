from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from medinv.modules.catalog.api.schemas import PartyCreate, ProductCreate, Review
from medinv.modules.catalog.application.service import CatalogService
from medinv.modules.procurement.api.schemas import (
    InspectionCreate,
    OrderCreate,
    ReceiptCreate,
    ReceiptPost,
    Transition,
)
from medinv.modules.procurement.application.service import ZONE, ProcurementService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def flow(world):
    maker = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    reviewer = replace(maker, principal_id=uuid4())
    catalog = CatalogService(world.uow)
    service = ProcurementService(world.uow)
    day = datetime.now(ZONE).date()
    product = catalog.create_product(
        maker,
        ProductCreate(
            code="P-NEW",
            name="采购测试商品",
            category="DEVICE",
            manufacturer_name="测试厂家",
            specification="单件",
            approval_number="DEMO-ONLY",
            valid_from=day - timedelta(days=3),
            valid_until=day + timedelta(days=300),
            sku_code="S-NEW",
            base_unit="个",
            qty_scale=0,
        ),
        str(uuid4()),
    )
    party = catalog.create_party(
        maker,
        PartyCreate(
            entity_id=world.entity,
            code="SUP-NEW",
            name="测试供应商",
            credit_code="DEMO",
            kind="SUPPLIER",
            license_no="DEMO-QUAL",
            valid_from=day - timedelta(days=3),
            valid_until=day + timedelta(days=300),
            categories=["DEVICE"],
        ),
        str(uuid4()),
    )
    review = Review(
        expected_version="1", decision="APPROVE", evidence_ref="TEST-FILE", reason="测试审核"
    )
    for kind, record in [("product", product), ("party", party)]:
        catalog.review(reviewer, kind, UUID(record["id"]), review, str(uuid4()))
    sku = UUID(product["sku_id"])

    def order(qty="10", price="13", tax="0.13", approve=True, lines=None):
        result = service.create_order(
            maker,
            OrderCreate(
                entity_id=world.entity,
                supplier_id=party["id"],
                warehouse_id=world.warehouse,
                lines=lines or [{"sku_id": sku, "qty": qty, "unit_price": price, "tax_rate": tax}],
            ),
            str(uuid4()),
        )
        oid = UUID(result["id"])
        if approve:
            service.transition(
                reviewer,
                oid,
                Transition(expected_version="1", reason="测试审批"),
                str(uuid4()),
                True,
            )
        return oid

    oid = order()

    def detail():
        return service.detail(maker, oid)

    def receive(qty="10", lot="B001", extra=None):
        doc = detail()
        line = {
            "order_line_id": doc["lines"][0]["id"],
            "location_id": world.location,
            "qty": qty,
            "lot_no": lot,
            "mfg_date": day - timedelta(days=2),
            "expiry_date": day + timedelta(days=365),
        }
        return service.receive(
            maker,
            oid,
            ReceiptCreate(
                expected_version=doc["order"]["version"],
                shipment_ref="DELIVERY",
                transport_note="常温随货资料齐全",
                lines=[line] if extra is None else extra,
            ),
            str(uuid4()),
        )

    def inspect(receipt_id, q="10", bad="0"):
        rec = next(r for r in detail()["receipts"] if r["id"] == str(receipt_id))
        return service.inspect(
            reviewer,
            UUID(str(receipt_id)),
            InspectionCreate(
                expected_version=rec["version"],
                note="质量验收记录",
                lines=[
                    {
                        "receipt_line_id": rec["lines"][0]["id"],
                        "qualified_qty": q,
                        "rejected_qty": bad,
                    }
                ],
            ),
            str(uuid4()),
        )

    def post(inspection, key=None):
        target = UUID(inspection["receipt_posting_id"])
        return service.post(
            reviewer,
            target,
            ReceiptPost(
                expected_version="1",
                inspection_id=inspection["inspection_id"],
                posting_segment_id=target,
            ),
            key or str(uuid4()),
        )

    return SimpleNamespace(**locals())


def new_stock(f):
    with f.world.uow.open(f.maker, readonly=True) as session:
        return session.execute(
            text("SELECT coalesce(sum(on_hand),0) FROM med.stock_balance WHERE sku_id=:id"),
            {"id": f.sku},
        ).scalar_one()


def test_partial_inspection_posts_stock_cost_and_payable_atomically(flow):
    f = flow
    options = f.service.options(f.maker, f.world.entity)
    assert [s["id"] for s in options["skus"]] == [str(f.sku)]
    assert len(options["suppliers"]) == 1
    receipt = f.receive()
    assert new_stock(f) == 0
    inspected = f.inspect(receipt["id"], "6", "2")
    assert new_stock(f) == 0
    result = f.post(inspected)
    assert new_stock(f) == 6
    assert f.world.scalar("SELECT sum(qty_delta) FROM med.stock_ledger") == 6
    assert f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 6
    assert f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("69.03")
    assert f.world.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == 78
    assert f.world.scalar("SELECT sum(tax_amount) FROM med.payable_obligation") == Decimal("8.97")
    second = f.inspect(receipt["id"], "2", "0")
    f.post(second)
    assert new_stock(f) == 8
    rec = f.detail()["receipts"][0]["lines"][0]
    assert Decimal(rec["custody_qty"]) == 2
    assert Decimal(rec["pending_qty"]) == 0
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 2
    assert (
        f.world.scalar(
            "SELECT count(*) FROM med.outbox_event WHERE event_type='inventory.receipt_posted'"
        )
        == 2
    )
    assert result["new_version"] == "2"


def test_over_receipt_and_multi_line_over_receipt_roll_back(flow):
    f = flow
    with pytest.raises(BusinessError) as err:
        f.receive("11")
    assert err.value.code == "OVER_RECEIPT"
    assert f.detail()["receipts"] == []
    source = f.detail()["lines"][0]["id"]
    lines = [
        {
            "order_line_id": source,
            "location_id": f.world.location,
            "qty": "6",
            "lot_no": lot,
            "mfg_date": f.day - timedelta(days=1),
            "expiry_date": f.day + timedelta(days=365),
        }
        for lot in ["L1", "L2"]
    ]
    with pytest.raises(BusinessError):
        f.receive(extra=lines)
    assert f.detail()["receipts"] == []
    assert Decimal(f.detail()["lines"][0]["received_qty"]) == 0


def test_three_arrivals_accumulate_and_close_order(flow):
    f = flow
    for qty in ["3", "4", "3"]:
        f.receive(qty)
    doc = f.detail()
    assert len(doc["receipts"]) == 3
    assert Decimal(doc["lines"][0]["received_qty"]) == 10
    assert doc["order"]["fulfillment_state"] == "RECEIVED"
    with pytest.raises(BusinessError):
        f.receive("1")
    assert new_stock(f) == 0


def test_concurrent_arrivals_cannot_exceed_remaining_quantity(flow):
    f = flow
    doc = f.detail()
    body = ReceiptCreate(
        expected_version=doc["order"]["version"],
        shipment_ref="PARALLEL",
        transport_note="TEST",
        lines=[
            {
                "order_line_id": doc["lines"][0]["id"],
                "location_id": f.world.location,
                "qty": "7",
                "lot_no": "C1",
                "mfg_date": f.day - timedelta(days=1),
                "expiry_date": f.day + timedelta(days=365),
            }
        ],
    )

    def receive(_):
        try:
            return f.service.receive(f.maker, f.oid, body, str(uuid4()))["state"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(receive, range(2)))
    assert results.count("PENDING_INSPECTION") == 1
    assert Decimal(f.detail()["lines"][0]["received_qty"]) == 7


def test_lot_conflict_rolls_back_arrival(flow):
    f = flow
    f.receive("3")
    doc = f.detail()
    body = ReceiptCreate(
        expected_version=doc["order"]["version"],
        shipment_ref="OTHER",
        transport_note="TEST",
        lines=[
            {
                "order_line_id": doc["lines"][0]["id"],
                "location_id": f.world.location,
                "qty": "3",
                "lot_no": "B001",
                "mfg_date": f.day - timedelta(days=2),
                "expiry_date": f.day + timedelta(days=400),
            }
        ],
    )
    with pytest.raises(BusinessError) as err:
        f.service.receive(f.maker, f.oid, body, str(uuid4()))
    assert err.value.code == "LOT_IDENTITY_CONFLICT"
    assert len(f.detail()["receipts"]) == 1


@pytest.mark.parametrize(
    "table,change",
    [
        ("party", "valid_until=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date-1"),
        ("party", "state='SUSPENDED'"),
        ("product", "approval_state='SUSPENDED'"),
        ("product", "restricted_category='COLD_CHAIN'"),
        ("sku", "serial_required=true"),
    ],
)
def test_qualification_rechecked_before_new_receipt(flow, table, change):
    f = flow
    f.world.execute(f"UPDATE med.{table} SET {change}")
    with pytest.raises(BusinessError):
        f.receive()
    assert f.detail()["receipts"] == []


def test_maker_checker_separation(flow):
    f = flow
    oid = f.order(approve=False)
    with pytest.raises(BusinessError) as err:
        f.service.transition(
            f.maker, oid, Transition(expected_version="1", reason="本人审批"), str(uuid4()), True
        )
    assert err.value.code == "SEPARATION_REQUIRED"
    receipt = f.receive()
    rec = f.detail()["receipts"][0]
    with pytest.raises(BusinessError) as err:
        f.service.inspect(
            f.maker,
            UUID(receipt["id"]),
            InspectionCreate(
                expected_version="1",
                note="本人验收",
                lines=[
                    {
                        "receipt_line_id": rec["lines"][0]["id"],
                        "qualified_qty": "10",
                        "rejected_qty": "0",
                    }
                ],
            ),
            str(uuid4()),
        )
    assert err.value.code == "SEPARATION_REQUIRED"


def test_post_retry_is_permanent_and_concurrent_safe(flow):
    f = flow
    receipt = f.receive()
    inspection = f.inspect(receipt["id"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: f.post(inspection), range(6)))
    assert len({r["posting_id"] for r in results}) == 1
    assert f.world.scalar("SELECT count(*) FROM med.stock_ledger") == 1
    assert f.world.scalar("SELECT count(*) FROM med.cost_ledger") == 1
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert new_stock(f) == 10


@pytest.mark.parametrize(
    "change", ["quality_blocked=true", "expiry_exclusive=clock_timestamp()-interval '1 second'"]
)
def test_changed_lot_state_blocks_posting_without_partial_facts(flow, change):
    f = flow
    receipt = f.receive()
    inspection = f.inspect(receipt["id"])
    f.world.execute(f"UPDATE med.lot SET {change}")
    with pytest.raises(BusinessError):
        f.post(inspection)
    assert new_stock(f) == 0
    assert f.world.scalar("SELECT count(*) FROM med.stock_ledger") == 0
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 0


def test_inspection_cannot_consume_more_than_pending(flow):
    f = flow
    receipt = f.receive()
    f.inspect(receipt["id"], "6", "2")
    with pytest.raises(BusinessError):
        f.inspect(receipt["id"], "3", "0")
    assert f.world.scalar("SELECT count(*) FROM med.inspection") == 1


def test_viewer_cross_entity_and_tenant_access_rejected(flow):
    f = flow
    viewer = replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    with pytest.raises(BusinessError):
        f.service.transition(
            viewer, f.oid, Transition(expected_version="2", reason="denied"), str(uuid4()), True
        )
    with pytest.raises(BusinessError):
        f.service.detail(replace(f.maker, entity_ids=frozenset()), f.oid)
    with pytest.raises(BusinessError):
        f.service.detail(replace(f.maker, tenant_id=uuid4()), f.oid)


def test_amount_overflow_rolls_back_every_posting_effect(flow):
    f = flow
    # One of two receipt lines exceeds the cost amount capacity.
    f.world.execute(
        "UPDATE med.purchase_order_line SET qty=2000,unit_price=99999999999999 WHERE order_id=:id",
        id=f.oid,
    )
    doc = f.detail()
    base = {
        "order_line_id": doc["lines"][0]["id"],
        "location_id": f.world.location,
        "mfg_date": f.day - timedelta(days=1),
        "expiry_date": f.day + timedelta(days=365),
    }
    receipt = f.receive(
        extra=[{**base, "qty": "1", "lot_no": "SMALL"}, {**base, "qty": "999", "lot_no": "LARGE"}]
    )
    rec = f.detail()["receipts"][0]
    inspection = f.service.inspect(
        f.reviewer,
        UUID(receipt["id"]),
        InspectionCreate(
            expected_version="1",
            note="验收",
            lines=[
                {"receipt_line_id": line["id"], "qualified_qty": line["qty"], "rejected_qty": "0"}
                for line in rec["lines"]
            ],
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError) as err:
        f.post(inspection)
    assert err.value.code == "BALANCE_OVERFLOW"
    assert new_stock(f) == 0
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 0
    assert f.world.scalar("SELECT count(*) FROM med.cost_balance") == 0
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 0


def test_closed_period_rejects_stock_cost_and_payable_together(flow):
    f = flow
    receipt = f.receive()
    inspection = f.inspect(receipt["id"])
    f.world.execute(
        """INSERT INTO med.accounting_period(tenant_id,entity_id,period,state)
        VALUES(:t,:e,:p,'CLOSED')""",
        t=f.maker.tenant_id,
        e=f.world.entity,
        p=f.day.strftime("%Y-%m"),
    )
    with pytest.raises(BusinessError) as error:
        f.post(inspection)
    assert error.value.code == "PERIOD_CLOSED"
    assert new_stock(f) == 0
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 0
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 0


def test_lost_authorization_guard_before_commit_rolls_back_posting(flow):
    from medinv.platform.database.commit_guard import before_business_commit

    f = flow
    receipt = f.receive()
    inspection = f.inspect(receipt["id"])

    def lost_connection():
        raise RuntimeError("simulated lost control connection")

    marker = before_business_commit.set(lost_connection)
    try:
        with pytest.raises(RuntimeError, match="simulated lost"):
            f.post(inspection)
    finally:
        before_business_commit.reset(marker)
    assert new_stock(f) == 0
    for table in ("posting_batch", "stock_ledger", "cost_ledger", "payable_obligation"):
        assert f.world.scalar(f"SELECT count(*) FROM med.{table}") == 0
    # The same business action can succeed after authorization is restored.
    f.post(inspection)
    assert new_stock(f) == 10
