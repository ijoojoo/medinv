from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.inventory.api.schemas import ReleaseRequest
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailComplete, RetailCreate, RetailPayment
from medinv.modules.retail.application import payments, posting
from medinv.modules.retail.application.service import RetailService
from medinv.modules.sales.api.schemas import ShipmentCreate
from medinv.modules.sales.application import fulfillment
from medinv.modules.settlement.api.schemas import AccountCreate
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def channels(s):
    retail = RetailService(s.f.world.uow)
    cashier = replace(s.f.maker, principal_id=uuid4(), permissions=ROLE_PERMISSIONS["CASHIER"])
    seller = replace(s.f.maker, principal_id=uuid4(), permissions=ROLE_PERMISSIONS["SALES"])
    retail_body = RetailCreate(
        client_operation_id=uuid4(),
        entity_id=s.f.world.entity,
        warehouse_id=s.f.world.warehouse,
        lines=[{"sku_id": s.f.sku, "qty": "7", "unit_price": "20", "tax_rate": "0.13"}],
    )
    wholesale_body = ShipmentCreate(
        expected_version=s.detail()["order"]["version"],
        client_operation_id=uuid4(),
        lines=[{"order_line_id": s.detail()["lines"][0]["id"], "qty": "7"}],
    )
    keys = {channel: str(uuid4()) for channel in ("retail", "wholesale")}

    def execute(channel):
        if channel == "retail":
            return retail.create(cashier, retail_body, keys[channel])
        return fulfillment.allocate(s.service, seller, s.oid, wholesale_body, keys[channel])

    def cancel(channel, result):
        body = Transition(expected_version=result["version"], reason="未交付，取消释放给另一渠道")
        if channel == "retail":
            return retail.cancel(cashier, UUID(result["id"]), body, str(uuid4()))
        return fulfillment.cancel(s.service, seller, UUID(result["id"]), body, str(uuid4()))

    return SimpleNamespace(**locals())


def test_wholesale_and_cashier_compete_for_one_batch_without_overselling(sale):
    s = sale
    c = channels(s)
    barrier = Barrier(2, timeout=10)

    def attempt(channel):
        barrier.wait()
        try:
            return channel, c.execute(channel)
        except BusinessError as error:
            return channel, error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(attempt, ("retail", "wholesale")))
    assert list(results.values()).count("INSUFFICIENT_STOCK") == 1
    winner = next(channel for channel, value in results.items() if isinstance(value, dict))
    assert s.scalar("reserved") == 7
    assert s.scalar("on_hand") == 10
    assert s.f.world.scalar("SELECT count(*) FROM med.shipment") == (winner == "wholesale")
    assert s.f.world.scalar("SELECT count(*) FROM med.retail_sale") == (winner == "retail")
    assert s.f.world.scalar("SELECT count(*) FROM med.inventory_demand") == 1 + (winner == "retail")
    assert s.f.world.scalar("SELECT count(*) FROM med.retail_payment") == 0
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    c.cancel(winner, results[winner])
    assert s.scalar("reserved") == 0


def test_seller_cancels_original_shipment_without_generic_inventory_release_or_other_scope(sale):
    s = sale
    c = channels(s)
    held = c.execute("wholesale")
    sh = s.manifest(held["id"])
    with pytest.raises(BusinessError) as error:
        s.service.inventory.release(
            c.seller,
            UUID(sh["reservation_id"]),
            ReleaseRequest(expected_version="1", reason="不允许通过通用库存接口释放"),
            str(uuid4()),
        )
    assert error.value.code == "FORBIDDEN"
    for actor, status in (
        (c.cashier, 403),
        (replace(c.seller, permissions=ROLE_PERMISSIONS["WAREHOUSE"]), 403),
        (replace(c.seller, entity_ids=frozenset([uuid4()])), 404),
    ):
        with pytest.raises(BusinessError) as error:
            fulfillment.cancel(
                s.service,
                actor,
                UUID(held["id"]),
                Transition(expected_version=held["version"], reason="越权验证"),
                str(uuid4()),
            )
        assert error.value.status == status
        assert s.scalar("reserved") == 7
    c.cancel("wholesale", held)
    assert s.scalar("reserved") == 0
    assert s.scalar("on_hand") == 10


@pytest.mark.parametrize("winner", ["retail", "wholesale"])
def test_shortage_retries_original_command_after_other_channel_releases_and_posts_once(
    sale, winner
):
    s = sale
    c = channels(s)
    loser = "retail" if winner == "wholesale" else "wholesale"
    held = c.execute(winner)
    with pytest.raises(BusinessError) as error:
        c.execute(loser)
    assert error.value.code == "INSUFFICIENT_STOCK"
    assert s.scalar("reserved") == 7
    assert s.f.world.scalar("SELECT count(*) FROM med.retail_payment") == 0
    c.cancel(winner, held)
    assert s.scalar("reserved") == 0
    recovered = c.execute(loser)  # Same actor, key, operation ID, body and original order/version.
    assert c.execute(loser) == recovered
    assert s.scalar("reserved") == 7
    sid = recovered["id"]
    if loser == "wholesale":
        s.pick(sid)
        s.verify(sid)
        first = s.post(sid)
        assert s.post(sid) == first
        assert s.f.world.scalar("SELECT sum(gross_amount) FROM med.receivable_obligation") == 140
        assert s.f.world.scalar("SELECT count(*) FROM med.retail_payment") == 0
    else:
        account = c.retail.finance.create_account(
            s.f.maker,
            AccountCreate(
                entity_id=s.f.world.entity, code="CASH", name="恢复验证现金", method="CASH"
            ),
            str(uuid4()),
        )
        payment = RetailPayment(
            client_operation_id=uuid4(),
            expected_version="1",
            account_id=account["id"],
            tendered="140",
            confirmed=True,
            reason="核对现金后收款",
        )
        key = str(uuid4())
        first = payments.receive(c.retail, c.cashier, UUID(sid), payment, key)
        assert payments.receive(c.retail, c.cashier, UUID(sid), payment, key) == first
        doc = c.retail.detail(c.cashier, UUID(sid))
        body = RetailComplete(
            expected_version=doc["sale"]["version"],
            reason="原单核对后交付",
            lines=[
                {"allocation_id": row["id"], "qty": row["allocated_qty"], "lot_no": row["lot_no"]}
                for row in doc["allocations"]
            ],
        )
        first = posting.complete(c.retail, c.cashier, UUID(sid), body, str(uuid4()))
        assert posting.complete(c.retail, c.cashier, UUID(sid), body, str(uuid4())) == first
        assert s.f.world.scalar("SELECT count(*) FROM med.retail_payment") == 1
        assert s.f.world.scalar("SELECT sum(amount) FROM med.retail_payment") == 140
        assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert s.scalar("on_hand") == 3
    assert s.scalar("reserved") == 0
    assert s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("34.512")
    assert s.f.world.scalar("SELECT count(*) FROM med.stock_ledger WHERE qty_delta<0") == 1
