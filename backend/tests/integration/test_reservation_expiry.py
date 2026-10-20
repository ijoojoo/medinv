import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_retail import retail as retail
from test_sales import sale as sale

from medinv.entrypoints.expiry_worker import ExpiryWorker
from medinv.modules.inventory.api.schemas import ReleaseRequest
from medinv.modules.inventory.application.expiry import ReservationExpiry
from medinv.platform.auth.maintenance import EXPIRY_ACTOR, MaintenanceGuard, expiry_context
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def expire_at(world, rid):
    world.execute(
        "UPDATE med.reservation SET expires_at=clock_timestamp()-interval '1 minute' WHERE id=:id",
        id=UUID(str(rid)),
    )


def release(world, rid):
    return ReservationExpiry(world.service).expire(
        expiry_context(world.ctx.tenant_id, world.entity), UUID(str(rid))
    )


def candidates(world, **kwargs):
    return ReservationExpiry(world.service).candidates(
        expiry_context(world.ctx.tenant_id), **kwargs
    )


def reserve(world, qty="2"):
    return UUID(world.service.reserve(world.ctx, world.demand(qty), str(uuid4()))["reservation_id"])


def test_expiry_releases_only_occupied_quantity_once_and_keeps_audit(world):
    rid = reserve(world)
    assert not release(world, rid)
    expire_at(world, rid)
    assert [r["id"] for r in candidates(world)] == [rid]
    assert release(world, rid)
    assert not release(world, rid)
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 0
    assert world.scalar("SELECT on_hand FROM med.stock_balance") == 10
    assert world.scalar("SELECT reserved_qty FROM med.inventory_demand_line") == 0
    assert world.scalar("SELECT state FROM med.reservation") == "RELEASED"
    assert world.scalar("SELECT auto_released_at IS NOT NULL FROM med.reservation")
    assert (
        world.scalar("SELECT created_by FROM med.audit_event WHERE action='inventory.auto_expired'")
        == EXPIRY_ACTOR
    )
    assert (
        world.scalar("SELECT count(*) FROM med.outbox_event WHERE event_type='inventory.released'")
        == 1
    )
    with pytest.raises(DBAPIError):
        world.execute("UPDATE med.reservation SET auto_released_at=NULL")


@pytest.mark.parametrize("protection", ["past_marker", "demand", "inactive_entity"])
def test_protection_and_inactive_entity_block_both_scan_and_execution(world, protection):
    rid = reserve(world)
    expire_at(world, rid)
    if protection == "past_marker":
        world.execute("UPDATE med.reservation SET protected_until=now()-interval '1 day'")
    elif protection == "demand":
        world.execute("UPDATE med.inventory_demand SET state='PAID_PROTECTED'")
    else:
        world.execute("UPDATE med.legal_entity SET status='SUSPENDED'")
    assert candidates(world) == []
    assert not release(world, rid)
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 2


def test_internal_permission_entity_scope_and_rls_are_required(world):
    rid = reserve(world)
    expire_at(world, rid)
    service = ReservationExpiry(world.service)
    with pytest.raises(BusinessError):
        service.expire(world.ctx, rid)
    with pytest.raises(BusinessError):
        service.expire(expiry_context(world.ctx.tenant_id, uuid4()), rid)
    assert not service.expire(expiry_context(uuid4(), world.entity), rid)
    assert all("inventory.expire" not in permissions for permissions in ROLE_PERMISSIONS.values())
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 2


def test_keyset_scan_cannot_lose_equal_deadlines_or_cross_tenants(world):
    ids = [reserve(world, "1") for _ in range(4)]
    world.execute("UPDATE med.reservation SET expires_at=now()-interval '1 minute'")
    first = candidates(world, limit=2)
    second = candidates(world, after=(first[-1]["expires_at"], first[-1]["id"]), limit=2)
    assert [r["id"] for r in first + second] == sorted(ids)
    with pytest.raises(ValueError):
        candidates(world, limit=101)


def test_two_workers_and_manual_release_do_not_double_release(world):
    rid = reserve(world)
    expire_at(world, rid)

    def attempt(kind):
        if kind == "manual":
            return world.service.release(
                world.ctx,
                rid,
                ReleaseRequest(expected_version="1", reason="人工取消"),
                str(uuid4()),
            )
        return release(world, rid)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(attempt, ["worker1", "worker2", "manual"]))
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 0
    assert world.scalar("SELECT released_qty FROM med.reservation_allocation") == 2
    assert (
        world.scalar("SELECT count(*) FROM med.outbox_event WHERE event_type='inventory.released'")
        == 1
    )


def test_busy_entity_is_skipped_then_retried_without_waiting(world):
    rid = reserve(world)
    expire_at(world, rid)
    with world.uow.open(world.ctx) as session:
        session.execute(text("SELECT id FROM med.legal_entity FOR UPDATE"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(release, world, rid).result(timeout=3) is False
    assert release(world, rid)


def test_allocated_wholesale_timeout_preserves_order_and_credit_and_can_reallocate(sale):
    s = sale
    sid = s.allocate("3")["id"]
    rid = s.f.world.scalar("SELECT reservation_id FROM med.shipment WHERE id=:id", id=UUID(sid))
    expire_at(s.f.world, rid)
    assert release(s.f.world, rid)
    doc = s.detail()
    assert doc["shipments"][0]["state"] == "CANCELLED"
    assert doc["shipments"][0]["auto_released_at"]
    assert doc["order"]["state"] == "APPROVED"
    assert doc["lines"][0]["remaining_qty"] == "10.000000"
    assert s.service.credit(s.f.maker, s.f.world.entity, s.customer)["exposure"] == "200.00"
    assert s.scalar("on_hand") == 10
    assert s.scalar("reserved") == 0
    assert s.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10
    assert s.f.world.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert s.manifest(s.allocate("10")["id"])["state"] == "ALLOCATED"


@pytest.mark.parametrize("stage", ["PICKED", "VERIFIED", "POSTED"])
def test_physical_shipment_and_posted_stock_never_auto_release(sale, stage):
    s = sale
    sid = s.allocate("3")["id"]
    s.pick(sid)
    if stage != "PICKED":
        s.verify(sid)
    if stage == "POSTED":
        s.post(sid)
    rid = s.f.world.scalar("SELECT reservation_id FROM med.shipment WHERE id=:id", id=UUID(sid))
    expire_at(s.f.world, rid)
    assert candidates(s.f.world) == []
    assert not release(s.f.world, rid)
    assert s.manifest(sid)["state"] == stage
    assert s.scalar("reserved") == (0 if stage == "POSTED" else 3)


def test_retail_expiry_cancels_source_without_cash_cost_or_posting(retail):
    r = retail
    sid = r.create()["id"]
    rid = r.detail(sid)["reservation"]["id"]
    expire_at(r.f.world, rid)
    assert release(r.f.world, rid)
    doc = r.detail(sid)
    assert doc["sale"]["state"] == "CANCELLED"
    assert doc["reservation"]["auto_released_at"]
    assert doc["payments"] == []
    assert r.f.world.scalar("SELECT state FROM med.inventory_demand") == "CANCELLED"
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
    assert (
        r.f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=r.f.sku)
        == 10
    )
    assert r.f.world.scalar("SELECT sum(qty) FROM med.cost_balance") == 10
    assert r.f.world.scalar("SELECT count(*) FROM med.posting_batch") == 1
    with pytest.raises(BusinessError):
        r.pay(sid)
    assert r.create(qty="10")["state"] == "RESERVED"


def test_stale_scan_rechecks_new_payment_protection_and_allows_completion(retail):
    r = retail
    sid = r.create()["id"]
    rid = r.detail(sid)["reservation"]["id"]
    expire_at(r.f.world, rid)
    assert candidates(r.f.world)
    # Simulate a stale scan followed by valid payment before a later deadline.
    r.f.world.execute("UPDATE med.reservation SET expires_at=now()+interval '1 hour'")
    r.pay(sid)
    expire_at(r.f.world, rid)
    assert not release(r.f.world, rid)
    assert candidates(r.f.world) == []
    assert r.complete(sid)["state"] == "COMPLETED"


def test_recorded_payment_blocks_expiry_even_with_inconsistent_protection_flags(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)
    rid = r.detail(sid)["reservation"]["id"]
    r.f.world.execute("UPDATE med.reservation SET protected_until=NULL")
    r.f.world.execute("UPDATE med.inventory_demand SET state='APPROVED'")
    r.f.world.execute("UPDATE med.retail_sale SET state='RESERVED'")
    expire_at(r.f.world, rid)
    assert candidates(r.f.world) == []
    assert not release(r.f.world, rid)
    assert len(r.detail(sid)["payments"]) == 1
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3


def test_payment_in_progress_cannot_be_interrupted_by_expiry(retail):
    r = retail
    sid = r.create()["id"]
    rid = r.detail(sid)["reservation"]["id"]
    started, resume = Event(), Event()

    def pay():
        def pause():
            started.set()
            assert resume.wait(5)

        with before_business_commit.set(pause):
            return r.pay(sid)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pay)
        try:
            assert started.wait(5)
            assert not release(r.f.world, rid)
        finally:
            resume.set()
        future.result(timeout=5)
    expire_at(r.f.world, rid)
    assert not release(r.f.world, rid)
    assert r.detail(sid)["sale"]["state"] == "PAID_PENDING_POST"
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3


def test_picking_in_progress_cannot_be_interrupted_by_expiry(sale):
    s = sale
    sid = s.allocate("3")["id"]
    rid = s.f.world.scalar("SELECT reservation_id FROM med.shipment WHERE id=:id", id=UUID(sid))
    started, resume = Event(), Event()

    def pick():
        def pause():
            started.set()
            assert resume.wait(5)

        with before_business_commit.set(pause):
            return s.pick(sid)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pick)
        try:
            assert started.wait(5)
            assert not release(s.f.world, rid)
        finally:
            resume.set()
        future.result(timeout=5)
    expire_at(s.f.world, rid)
    assert not release(s.f.world, rid)
    assert s.manifest(sid)["state"] == "PICKED"
    assert s.scalar("reserved") == 3


def test_commit_guard_failure_rolls_back_source_reservation_audit_and_event(retail):
    r = retail
    sid = r.create()["id"]
    rid = r.detail(sid)["reservation"]["id"]
    expire_at(r.f.world, rid)
    audit_count = r.f.world.scalar("SELECT count(*) FROM med.audit_event")
    events = r.f.world.scalar("SELECT count(*) FROM med.outbox_event")

    def failed_guard():
        raise BusinessError("TENANT_UNAVAILABLE", "企业锁失效", 409)

    with before_business_commit.set(failed_guard), pytest.raises(BusinessError):
        release(r.f.world, rid)
    assert r.detail(sid)["sale"]["state"] == "RESERVED"
    assert r.detail(sid)["reservation"]["auto_released_at"] is None
    assert r.f.world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3
    assert r.f.world.scalar("SELECT count(*) FROM med.audit_event") == audit_count
    assert r.f.world.scalar("SELECT count(*) FROM med.outbox_event") == events
    assert release(r.f.world, rid)


@pytest.fixture
def maintenance(world, settings):
    owner = create_engine(os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True)
    runtime = create_engine(settings.control_database_url.get_secret_value(), hide_parameters=True)
    tenant = world.ctx.tenant_id
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("INSERT INTO control.tenant(id,name) VALUES (:id,'Expiry test')"), {"id": tenant}
        )

    class ScopedGuard(MaintenanceGuard):
        def tenants(self, after, limit=10):
            # Isolate this test's worker from other tenants in the shared integration database.
            return [tenant] if after.int == 0 else []

    yield ScopedGuard(runtime), owner
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(text("UPDATE control.tenant SET active=false WHERE id=:id"), {"id": tenant})
    runtime.dispose()
    owner.dispose()


def test_real_worker_uses_runtime_guard_bounded_batches_and_restart_safety(
    world, settings, maintenance
):
    guard, _ = maintenance
    ids = [reserve(world, "1") for _ in range(3)]
    for rid in ids:
        expire_at(world, rid)
    cfg = settings.model_copy(
        update={"reservation_expiry_enabled": True, "reservation_expiry_batch_size": 2}
    )
    worker = ExpiryWorker(ReservationExpiry(world.service), guard, cfg)
    assert worker.tick() == dict(scanned=2, released=2, skipped=0, errors=0)
    assert worker.tick()["scanned"] == 0  # Tenant cursor wraps.
    assert worker.tick()["released"] == 1
    assert ExpiryWorker(ReservationExpiry(world.service), guard, cfg).tick()["scanned"] == 0
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 0


def test_disabled_tenant_cannot_commit_and_runtime_cannot_change_authorization(world, maintenance):
    guard, owner = maintenance
    rid = reserve(world)
    expire_at(world, rid)
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.tenant SET active=false WHERE id=:id"), {"id": world.ctx.tenant_id}
        )
    with pytest.raises(BusinessError), guard.hold(world.ctx.tenant_id):
        release(world, rid)
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 2
    with guard.engine.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("UPDATE control.tenant SET active=true WHERE id=:id"), {"id": world.ctx.tenant_id}
        )


def test_tenant_disable_serializes_after_guarded_business_commit(world, maintenance):
    guard, owner = maintenance
    rid = reserve(world)
    expire_at(world, rid)
    started, finished = Event(), Event()

    def disable():
        with owner.begin() as conn:
            conn.execute(text("SET ROLE control_owner"))
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            started.set()
            conn.execute(
                text("UPDATE control.tenant SET active=false WHERE id=:id"),
                {"id": world.ctx.tenant_id},
            )
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with guard.hold(world.ctx.tenant_id):
            future = pool.submit(disable)
            assert started.wait(2)
            assert not finished.wait(0.1)
            assert release(world, rid)
        future.result(timeout=5)
    assert finished.is_set()
    with pytest.raises(BusinessError), guard.hold(world.ctx.tenant_id):
        pass


def test_worker_disabled_and_busy_old_row_do_not_starve_later_rows(world, settings, maintenance):
    guard, _ = maintenance
    ids = [reserve(world, "1") for _ in range(2)]
    for rid in ids:
        expire_at(world, rid)
    cfg = settings.model_copy(
        update={"reservation_expiry_enabled": False, "reservation_expiry_batch_size": 1}
    )
    worker = ExpiryWorker(ReservationExpiry(world.service), guard, cfg)
    assert worker.tick()["scanned"] == 0
    cfg.reservation_expiry_enabled = True
    with world.uow.open(world.ctx) as session:
        session.execute(text("SELECT id FROM med.legal_entity FOR UPDATE"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(worker.tick).result(timeout=3)["skipped"] == 1
    worker.tick()  # Tenant cursor wraps.
    assert worker.tick()["released"] == 1  # Second candidate, not the busy old row.
    assert world.scalar("SELECT state FROM med.reservation WHERE id=:id", id=ids[0]) == "ACTIVE"
    for _ in range(5):
        worker.tick()
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 0


def test_worker_rolls_back_failed_item_and_continues_other_candidates(world, settings, maintenance):
    guard, _ = maintenance
    ids = [reserve(world, "1") for _ in range(2)]
    for rid in ids:
        expire_at(world, rid)

    class FailsFirst(ReservationExpiry):
        def expire(self, ctx, rid):
            if rid == ids[0]:

                def fail():
                    raise BusinessError("TEST_GUARD_LOST", "模拟提交失败", 409)

                with before_business_commit.set(fail):
                    return super().expire(ctx, rid)
            return super().expire(ctx, rid)

    cfg = settings.model_copy(update={"reservation_expiry_enabled": True})
    worker = ExpiryWorker(FailsFirst(world.service), guard, cfg)
    assert worker.tick() == dict(scanned=2, released=1, skipped=0, errors=1)
    assert world.scalar("SELECT reserved FROM med.stock_balance") == 1
    assert release(world, ids[0])
