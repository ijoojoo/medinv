from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.inventory.api.counts import CountCreate
from medinv.modules.inventory.api.location_moves import (
    LocationMoveCreate,
    LocationMovePost,
    LocationMoveReview,
)
from medinv.modules.inventory.api.transfers import WarehouseCreate
from medinv.modules.inventory.api.warehouses import LocationCreate
from medinv.modules.inventory.application import location_move_posting
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.inventory.application.location_moves import LocationMoveService
from medinv.modules.inventory.application.warehouses import WarehouseService
from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def moved(sale):
    s = sale
    f = s.f
    w = f.world
    service = LocationMoveService(w.uow)
    warehouses = WarehouseService(w.uow)
    dest = warehouses.add_location(
        f.maker,
        w.warehouse,
        LocationCreate(
            client_operation_id=uuid4(),
            expected_version="1",
            code="MOVE-TO",
            name="移库目标",
            reason="货架整理",
        ),
        str(uuid4()),
    )
    source = w.scalar("SELECT id FROM med.stock_balance WHERE sku_id=:s", s=f.sku)
    source_location = w.scalar("SELECT location_id FROM med.stock_balance WHERE id=:id", id=source)

    def body(qty="4", **changes):
        return LocationMoveCreate(
            **(
                dict(
                    client_operation_id=uuid4(),
                    entity_id=w.entity,
                    balance_id=source,
                    destination_location_id=dest["id"],
                    qty=qty,
                    lot_no="B001",
                    reason="货架整理",
                )
                | changes
            )
        )

    def create(qty="4", request=None, ctx=None):
        return UUID(service.create(ctx or f.maker, request or body(qty), str(uuid4()))["id"])

    def detail(mid):
        return service.detail(f.maker, mid)

    def transition(mid):
        return Transition(expected_version=detail(mid)["document"]["version"], reason="实物核对")

    def review(mid, ctx=None, decision="APPROVE"):
        return service.transition(
            ctx or f.reviewer,
            mid,
            LocationMoveReview(**transition(mid).model_dump(), decision=decision),
            str(uuid4()),
            True,
        )

    def post(mid, request=None, ctx=None):
        return location_move_posting.post(
            service,
            ctx or f.maker,
            mid,
            request
            or LocationMovePost(**transition(mid).model_dump(), lot_no="B001", confirmed=True),
            str(uuid4()),
        )

    def ready(qty="4"):
        mid = create(qty)
        review(mid)
        return mid

    def stock(location):
        return w.scalar(
            "SELECT coalesce(sum(on_hand),0) FROM med.stock_balance "
            "WHERE location_id=:l AND sku_id=:s",
            l=location,
            s=f.sku,
        )

    return SimpleNamespace(**locals())


def test_partial_move_keeps_cost_and_obligations_and_posts_two_stock_effects(moved):
    x = moved
    cost = x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku)
    costs = x.w.scalar("SELECT count(*) FROM med.cost_ledger")
    mid = x.ready()
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0
    first = x.post(mid)
    assert x.post(mid) == first
    assert x.stock(x.source_location) == 6 and x.stock(x.dest["id"]) == 4
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == cost
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 10
    assert x.w.scalar("SELECT count(*) FROM med.cost_ledger") == costs
    assert x.w.scalar("SELECT sum(gross_amount) FROM med.payable_obligation") == Decimal("130")
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert x.w.scalar("SELECT count(*) FROM med.stock_ledger WHERE event_type='LOCATION_MOVE'") == 2
    assert (
        x.w.scalar("SELECT sum(qty_delta) FROM med.stock_ledger WHERE event_type='LOCATION_MOVE'")
        == 0
    )
    assert first["cost_delta"] == "0.00000000"
    assert x.w.scalar("SELECT count(*) FROM med.outbox_event WHERE aggregate_id=:id", id=mid) == 1
    # Ordinary wholesale fulfillment can allocate and post across both locations.
    x.s.post(x.s.ready("10"))
    assert x.stock(x.source_location) == x.stock(x.dest["id"]) == 0
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 0


def test_reverse_move_merges_existing_balance_without_duplicate_stock(moved):
    x = moved
    x.post(x.ready())
    target = x.w.scalar(
        "SELECT id FROM med.stock_balance WHERE sku_id=:s AND location_id=:l",
        s=x.f.sku,
        l=x.dest["id"],
    )
    mid = x.create(
        request=x.body("2", balance_id=target, destination_location_id=x.source_location)
    )
    x.review(mid)
    x.post(mid)
    assert x.stock(x.source_location) == 8 and x.stock(x.dest["id"]) == 2
    assert x.w.scalar("SELECT count(*) FROM med.stock_balance WHERE sku_id=:s", s=x.f.sku) == 2


def test_sales_reservation_after_review_cannot_be_moved(moved):
    x = moved
    mid = x.ready()
    x.s.allocate("7")
    with pytest.raises(BusinessError) as err:
        x.post(mid)
    assert err.value.code == "INSUFFICIENT_STOCK"
    assert x.detail(mid)["document"]["state"] == "APPROVED"
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0
    x.post(x.ready("3"))
    assert x.w.scalar("SELECT reserved FROM med.stock_balance WHERE id=:id", id=x.source) == 7
    assert x.stock(x.source_location) == 7


def test_independent_review_version_and_cancel_do_not_change_stock(moved):
    x = moved
    mid = x.create()
    with pytest.raises(BusinessError) as err:
        x.review(mid, ctx=x.f.maker)
    assert err.value.code == "MAKER_CHECKER"
    with pytest.raises(BusinessError) as err:
        x.post(mid)
    assert err.value.code == "MOVE_NOT_APPROVED"
    x.review(mid)
    with pytest.raises(BusinessError):
        x.service.transition(
            x.f.maker, mid, Transition(expected_version="1", reason="旧版取消"), str(uuid4())
        )
    x.service.transition(x.f.maker, mid, x.transition(mid), str(uuid4()))
    with pytest.raises(BusinessError):
        x.post(mid)
    rejected = x.create()
    x.review(rejected, decision="REJECT")
    assert x.detail(rejected)["document"]["state"] == "CANCELLED"
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0


@pytest.mark.parametrize("target", ["same", "cross", "unknown"])
def test_destination_must_be_visible_different_location_in_same_warehouse(moved, target):
    x = moved
    destination = x.source_location if target == "same" else uuid4()
    if target == "cross":
        warehouse = x.warehouses.create(
            x.f.maker,
            WarehouseCreate(
                client_operation_id=uuid4(),
                entity_id=x.w.entity,
                code="OTHER",
                name="其他仓",
                location_code="A1",
            ),
            str(uuid4()),
        )
        destination = warehouse["location_id"]
    with pytest.raises(BusinessError):
        x.create(request=x.body(destination_location_id=destination))
    assert x.w.scalar("SELECT count(*) FROM med.location_move") == 0


@pytest.mark.parametrize("change", ["gate", "lot", "expiry", "product"])
def test_post_rechecks_changed_batch_eligibility(moved, change):
    x = moved
    mid = x.ready()
    sql = {
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
        "lot": "UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s",
        "expiry": "UPDATE med.lot SET expiry_exclusive=now()-interval '1 day' WHERE sku_id=:s",
        "product": "UPDATE med.product SET approval_state='SUSPENDED' WHERE id="
        "(SELECT product_id FROM med.sku WHERE id=:s)",
    }
    x.w.execute(sql[change], s=x.f.sku)
    with pytest.raises(BusinessError) as err:
        x.post(mid)
    assert err.value.code == "TRANSFER_INELIGIBLE"
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0


def test_count_freeze_blocks_move_and_resumes_after_count_cancel(moved):
    x = moved
    mid = x.ready()
    counts = CountService(x.w.uow)
    cid = UUID(
        counts.create(
            x.f.maker,
            CountCreate(
                client_operation_id=uuid4(),
                entity_id=x.w.entity,
                warehouse_id=x.w.warehouse,
                balance_ids=[x.source],
                reason="先盘点",
            ),
            str(uuid4()),
        )["id"]
    )
    with pytest.raises(BusinessError) as err:
        x.post(mid)
    assert err.value.code == "COUNT_FROZEN"
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0
    counts.cancel(x.f.maker, cid, Transition(expected_version="1", reason="先移库"), str(uuid4()))
    x.post(mid)
    assert x.stock(x.dest["id"]) == 4


def test_create_retry_and_concurrent_post_execute_once(moved):
    x = moved
    body = x.body()
    mid = x.create(request=body)
    assert x.create(request=body) == mid
    with pytest.raises(BusinessError) as err:
        x.create(request=body.model_copy(update={"reason": "changed"}))
    assert err.value.code == "OPERATION_CONFLICT"
    x.review(mid)
    request = LocationMovePost(**x.transition(mid).model_dump(), lot_no="B001", confirmed=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: x.post(mid, request=request), range(2)))
    assert results[0] == results[1]
    assert x.stock(x.dest["id"]) == 4


def test_competing_moves_cannot_overdraw_source(moved):
    x = moved
    mids = [x.ready("6"), x.ready("6")]

    def post(mid):
        try:
            return x.post(mid)["state"]
        except BusinessError as err:
            return err.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, mids))
    assert sorted(results) == ["INSUFFICIENT_STOCK", "POSTED"]
    assert x.stock(x.source_location) == 4 and x.stock(x.dest["id"]) == 6


def test_commit_revocation_rolls_back_source_destination_posting_and_outbox(moved):
    x = moved
    mid = x.ready()
    before = {
        table: x.w.scalar(f"SELECT count(*) FROM med.{table}")
        for table in (
            "stock_balance",
            "stock_ledger",
            "posting_batch",
            "audit_event",
            "outbox_event",
            "idempotency_record",
        )
    }

    def revoked():
        raise BusinessError("FORBIDDEN", "提交前权限撤销", 403)

    token = before_business_commit.set(revoked)
    try:
        with pytest.raises(BusinessError):
            x.post(mid)
    finally:
        before_business_commit.reset(token)
    assert x.detail(mid)["document"]["state"] == "APPROVED"
    assert x.stock(x.source_location) == 10 and x.stock(x.dest["id"]) == 0
    assert before == {table: x.w.scalar(f"SELECT count(*) FROM med.{table}") for table in before}


@pytest.mark.parametrize("scope", ["tenant", "entity", "permission"])
def test_read_write_and_destinations_are_fenced(moved, scope):
    x = moved
    mid = x.ready()
    changes = {
        "tenant": {"tenant_id": uuid4()},
        "entity": {"entity_ids": frozenset()},
        "permission": {"permissions": frozenset()},
    }
    ctx = replace(x.f.maker, **changes[scope])
    for action in (
        lambda: x.service.detail(ctx, mid),
        lambda: x.create(ctx=ctx),
        lambda: x.post(mid, ctx=ctx),
        lambda: x.service.destinations(ctx, x.w.entity, x.source, 0),
    ):
        with pytest.raises(BusinessError):
            action()
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert x.service.detail(viewer, mid)["document"]["state"] == "APPROVED"
    with pytest.raises(BusinessError):
        x.create(ctx=viewer)


def test_wrong_lot_fractional_quantity_and_unconfirmed_move_have_no_effect(moved):
    x = moved
    for data in ({"lot_no": "wrong"}, {"qty": "0.1"}, {"qty": "11"}):
        with pytest.raises(BusinessError):
            x.create(request=x.body(**data))
    mid = x.ready()
    with pytest.raises(ValidationError):
        LocationMovePost(**x.transition(mid).model_dump(), lot_no="B001", confirmed=False)
    with pytest.raises(BusinessError) as err:
        x.post(
            mid,
            request=LocationMovePost(
                **x.transition(mid).model_dump(), lot_no="wrong", confirmed=True
            ),
        )
    assert err.value.code == "MOVE_LOT_MISMATCH"
    assert x.stock(x.dest["id"]) == 0


def test_item_and_posted_facts_are_immutable(moved):
    x = moved
    mid = x.ready()
    with pytest.raises(DBAPIError):
        x.w.execute("UPDATE med.location_move_item SET qty=1 WHERE move_id=:id", id=mid)
    with pytest.raises(DBAPIError):
        x.w.execute(
            "UPDATE med.location_move SET destination_location_id=:l WHERE id=:id",
            id=mid,
            l=x.source_location,
        )
    x.post(mid)
    with pytest.raises(DBAPIError):
        x.w.execute("UPDATE med.location_move SET reason='changed' WHERE id=:id", id=mid)


def test_destination_options_exclude_source_and_are_paginated(moved):
    x = moved
    first = x.service.destinations(x.f.maker, x.w.entity, x.source, 0)
    assert str(x.source_location) not in [r["id"] for r in first["items"]]
    assert str(x.dest["id"]) in [r["id"] for r in first["items"]]
    assert first["has_more"] is False
    assert x.service.destinations(x.f.maker, x.w.entity, x.source, 200)["items"] == []
