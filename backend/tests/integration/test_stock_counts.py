import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow

from medinv.modules.inventory.api.counts import CountCreate, CountReview, CountSubmit
from medinv.modules.inventory.application import count_posting
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def counted(flow):
    f = flow
    w = f.world
    f.post(f.inspect(f.receive()["id"]))
    service = CountService(w.uow)
    balance = w.scalar("SELECT id FROM med.stock_balance WHERE sku_id=:s", s=f.sku)

    def create(ids=None, body=None, ctx=None):
        return UUID(
            service.create(
                ctx or f.maker,
                body
                or CountCreate(
                    client_operation_id=uuid4(),
                    entity_id=w.entity,
                    warehouse_id=w.warehouse,
                    balance_ids=ids or [balance],
                    reason="仓库例行实盘",
                ),
                str(uuid4()),
            )["id"]
        )

    def detail(cid):
        return service.detail(f.maker, cid)

    def transition(cid):
        return Transition(
            expected_version=detail(cid)["document"]["version"], reason="核对本次实盘"
        )

    def submit(cid, qty="8", unit=None, ctx=None, overrides=None):
        lines = [
            {
                "line_id": r["id"],
                "actual_qty": qty,
                "lot_no": r["lot_no"],
                "gain_unit_cost": unit,
                "note": "复盘确认数量，成本依据随原批次",
            }
            for r in detail(cid)["lines"]
        ]
        if overrides:
            lines[0].update(overrides)
        return service.submit(
            ctx or f.maker,
            cid,
            CountSubmit(**transition(cid).model_dump(), lines=lines),
            str(uuid4()),
        )

    def review(cid, decision="APPROVE", ctx=None):
        return service.review(
            ctx or f.reviewer,
            cid,
            CountReview(**transition(cid).model_dump(), decision=decision),
            str(uuid4()),
        )

    def post(cid, ctx=None, body=None):
        return count_posting.post(
            service, ctx or f.reviewer, cid, body or transition(cid), str(uuid4())
        )

    def ready(qty="8", unit=None):
        cid = create()
        submit(cid, qty, unit)
        review(cid)
        return cid

    def stock():
        return w.scalar("SELECT on_hand FROM med.stock_balance WHERE id=:id", id=balance)

    def maintenance(cid, sql, **params):
        # Deliberately bypass the application lifecycle in this isolated fixture to prove
        # snapshots still reject unexpected maintenance changes after a freeze was bypassed.
        state = detail(cid)["document"]["state"]
        with w.uow.open(f.maker) as session:
            session.execute(
                text("UPDATE med.stock_count SET state='CANCELLED' WHERE id=:id"), {"id": cid}
            )
            session.execute(text(sql), params)
            session.execute(
                text("UPDATE med.stock_count SET state=:state WHERE id=:id"),
                {"id": cid, "state": state},
            )

    return SimpleNamespace(**locals())


@pytest.mark.parametrize(
    "actual,cost,changed",
    [("8", "92.032", 1), ("12", "138.048", 1), ("10", "115.04", 0), ("0", "0", 1)],
)
def test_count_gain_loss_zero_and_no_difference_post_atomically(counted, actual, cost, changed):
    x = counted
    cid = x.ready(actual)
    assert x.stock() == 10
    result = x.post(cid)
    assert x.post(cid) == result
    assert result["changed_lines"] == changed
    assert x.stock() == Decimal(actual)
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal(cost)
    assert x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert x.w.scalar("SELECT count(*) FROM med.posting_batch WHERE source_type='STOCK_COUNT'") == 1
    assert (
        x.w.scalar(
            "SELECT count(*) FROM med.stock_ledger WHERE event_type IN ('COUNT_GAIN','COUNT_LOSS')"
        )
        == changed
    )
    assert (
        x.w.scalar(
            "SELECT count(*) FROM med.outbox_event WHERE event_type='inventory.count_posted'"
        )
        == 1
    )
    if changed:
        ledger = x.f.service.ledger(x.f.maker, x.w.entity, 0)["items"]
        assert ledger[0]["posting_id"] == result["posting_id"]
        assert ledger[0]["financial_kind"] is None


def test_count_requires_all_readings_correct_lot_and_sku_scale(counted):
    x = counted
    cid = x.create()
    for overrides, code in [
        ({"actual_qty": "1.1"}, "QTY_SCALE_INVALID"),
        ({"lot_no": "WRONG"}, "COUNT_LOT_MISMATCH"),
        ({"line_id": str(uuid4())}, "COUNT_LINES"),
        ({"gain_unit_cost": "5"}, "COUNT_COST_POLICY"),
    ]:
        with pytest.raises(BusinessError) as exc:
            x.submit(cid, overrides=overrides)
        assert exc.value.code == code
    assert x.w.scalar("SELECT count(*) FROM med.stock_count_measurement") == 0


def test_independent_checker_excludes_creator_and_different_counter(counted):
    x = counted
    cid = x.create()
    counter = replace(x.f.maker, principal_id=uuid4())
    x.submit(cid, ctx=counter)
    for actor in (x.f.maker, counter):
        with pytest.raises(BusinessError) as exc:
            x.review(cid, ctx=actor)
        assert exc.value.code == "MAKER_CHECKER"
    x.review(cid)
    for actor in (x.f.maker, counter):
        with pytest.raises(BusinessError):
            x.post(cid, ctx=actor)
    x.post(cid)


@pytest.mark.parametrize("stage", ["submit", "review", "post"])
def test_stock_changes_require_recount_at_each_boundary(counted, stage):
    x = counted
    cid = x.create()
    if stage != "submit":
        x.submit(cid)
    if stage == "post":
        x.review(cid)
    x.maintenance(
        cid, "UPDATE med.stock_balance SET on_hand=9,version=version+1 WHERE id=:id", id=x.balance
    )
    with pytest.raises(BusinessError) as exc:
        getattr(x, stage)(cid)
    assert exc.value.code == "COUNT_STALE"
    assert x.stock() == 9
    x.service.cancel(x.f.maker, cid, x.transition(cid), str(uuid4()))
    assert x.detail(x.create())["lines"][0]["book_qty"] == "9.000000"


def test_shared_cost_pool_change_rejects_even_if_stock_row_unchanged(counted):
    x = counted
    cid = x.ready()
    x.maintenance(cid, "UPDATE med.cost_balance SET version=version+1")
    with pytest.raises(BusinessError) as exc:
        x.post(cid)
    assert exc.value.code == "COUNT_STALE"


def test_count_requires_reservations_and_blocked_quantities_resolved_before_freezing(counted):
    x = counted
    x.w.execute(
        "UPDATE med.stock_balance SET reserved=4,blocked=2,version=version+1 WHERE id=:id",
        id=x.balance,
    )
    with pytest.raises(BusinessError) as exc:
        x.create()
    assert exc.value.code == "COUNT_COMMITTED_STOCK"
    x.w.execute(
        "UPDATE med.stock_balance SET reserved=0,blocked=0,version=version+1 WHERE id=:id",
        id=x.balance,
    )
    assert x.detail(x.create())["document"]["state"] == "COUNTING"


@pytest.mark.parametrize("kind", ["lot", "product", "gate"])
def test_gain_revalidates_quality_and_qualification_at_post(counted, kind):
    x = counted
    cid = x.ready("12")
    statements = {
        "lot": "UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s",
        "product": (
            "UPDATE med.product SET approval_state='SUSPENDED' "
            "WHERE id=(SELECT product_id FROM med.sku WHERE id=:s)"
        ),
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
    }
    x.w.execute(statements[kind], s=x.f.sku)
    with pytest.raises(BusinessError) as exc:
        x.post(cid)
    assert exc.value.code == "COUNT_GAIN_INELIGIBLE"
    assert x.stock() == 10


def test_zero_quantity_pool_gain_needs_approved_explicit_cost(counted):
    x = counted
    x.post(x.ready("0"))
    cid = x.create()
    with pytest.raises(BusinessError) as exc:
        x.submit(cid, "2")
    assert exc.value.code == "COUNT_GAIN_COST_REQUIRED"
    x.submit(cid, "2", "11.504")
    x.review(cid)
    x.post(cid)
    assert x.stock() == 2
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == Decimal("23.008")


def test_duplicate_active_count_reject_cancel_and_reject_release_scope(counted):
    x = counted
    cid = x.create()
    with pytest.raises(BusinessError) as exc:
        x.create()
    assert exc.value.code == "COUNT_EXISTS"
    x.submit(cid)
    x.review(cid, "REJECT")
    assert x.detail(cid)["document"]["state"] == "CANCELLED"
    with pytest.raises(BusinessError):
        x.post(cid)
    cid = x.create()
    x.service.cancel(x.f.maker, cid, x.transition(cid), str(uuid4()))
    assert x.stock() == 10
    assert x.detail(x.create())["document"]["state"] == "COUNTING"


def test_create_permanent_retry_and_concurrent_post_only_apply_once(counted):
    x = counted
    body = CountCreate(
        client_operation_id=uuid4(),
        entity_id=x.w.entity,
        warehouse_id=x.w.warehouse,
        balance_ids=[x.balance],
        reason="永久操作防重",
    )
    cid = x.create(body=body)
    assert x.create(body=body) == cid
    with pytest.raises(BusinessError) as exc:
        x.create(body=body.model_copy(update={"reason": "changed"}))
    assert exc.value.code == "OPERATION_CONFLICT"
    x.submit(cid)
    x.review(cid)
    transition = x.transition(cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: x.post(cid, body=transition), range(2)))
    assert results[0] == results[1]
    assert x.stock() == 8


def test_count_commit_guard_rolls_back_all_effects(counted):
    x = counted
    cid = x.ready()
    before = x.w.scalar("SELECT count(*) FROM med.audit_event")

    def fail():
        raise BusinessError("TEST_REVOKED", "授权失效", 403)

    marker = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            x.post(cid)
    finally:
        before_business_commit.reset(marker)
    assert x.stock() == 10
    assert x.detail(cid)["document"]["state"] == "APPROVED"
    assert x.w.scalar("SELECT count(*) FROM med.posting_batch WHERE source_type='STOCK_COUNT'") == 0
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == before


def test_count_permissions_entity_rls_and_immutable_snapshots(counted):
    x = counted
    cid = x.create()
    x.submit(cid)
    for actor in [
        replace(x.f.maker, tenant_id=uuid4()),
        replace(x.f.maker, entity_ids=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            x.service.detail(actor, cid)
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert x.service.detail(viewer, cid)["document"]["id"] == str(cid)
    with pytest.raises(BusinessError):
        x.create(ctx=viewer)
    with pytest.raises(BusinessError):
        x.review(cid, ctx=viewer)
    for table in ["stock_count_line", "stock_count_measurement"]:
        with pytest.raises(DBAPIError):
            x.w.execute(f"DELETE FROM med.{table}")
    with pytest.raises(DBAPIError):
        x.w.execute("UPDATE med.stock_count_measurement SET actual_qty=999")
    assert x.stock() == 10


def test_closed_period_preserves_approved_count_without_posting(counted):
    x = counted
    cid = x.ready()
    owner = create_engine(os.environ["MED_MIGRATION_URL"], hide_parameters=True)
    try:
        with owner.begin() as connection:
            connection.execute(text("SET ROLE med_owner"))
            connection.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(x.f.maker.tenant_id)}
            )
            connection.execute(
                text("UPDATE med.accounting_period SET state='CLOSED' WHERE entity_id=:e"),
                {"e": x.w.entity},
            )
    finally:
        owner.dispose()
    with pytest.raises(BusinessError) as exc:
        x.post(cid)
    assert exc.value.code == "PERIOD_CLOSED"
    assert x.stock() == 10


def test_multiple_locations_share_one_pool_and_clear_last_cost_residual(counted):
    x = counted
    location, balance = uuid4(), uuid4()
    x.w.execute(
        """INSERT INTO med.location(tenant_id,id,warehouse_id,code,created_by,updated_by)
        VALUES(:t,:id,:w,'L2',:a,:a)""",
        t=x.f.maker.tenant_id,
        id=location,
        w=x.w.warehouse,
        a=x.f.maker.principal_id,
    )
    x.w.execute(
        """UPDATE med.stock_balance SET on_hand=3,version=version+1 WHERE id=:id""", id=x.balance
    )
    x.w.execute(
        """INSERT INTO med.stock_balance(tenant_id,id,entity_id,owner_id,warehouse_id,
        location_id,sku_id,lot_id,quality_status,on_hand,created_by,updated_by)
        SELECT tenant_id,:id,entity_id,owner_id,warehouse_id,:loc,sku_id,lot_id,quality_status,7,
        created_by,updated_by FROM med.stock_balance WHERE id=:source""",
        id=balance,
        loc=location,
        source=x.balance,
    )
    cid = x.create(ids=[x.balance, balance])
    x.submit(cid, "0")
    x.review(cid)
    result = x.post(cid)
    assert result["changed_lines"] == 2 and result["loss_cost"] == "115.04000000"
    assert x.w.scalar("SELECT amount FROM med.cost_balance") == 0
    assert x.w.scalar("SELECT sum(amount_delta) FROM med.cost_ledger") == 0
    assert x.w.scalar("SELECT sum(on_hand) FROM med.stock_balance WHERE sku_id=:s", s=x.f.sku) == 0


def test_count_candidates_do_not_include_seed_without_cost_and_scope_checks(counted):
    x = counted
    items = x.service.candidates(x.f.maker, x.w.entity, x.w.warehouse, "", 0)["items"]
    assert [r["id"] for r in items] == [str(x.balance)]
    assert x.service.candidates(x.f.maker, x.w.entity, x.w.warehouse, "not found", 0)["items"] == []
    with pytest.raises(BusinessError):
        x.create(ids=[x.w.balance])
    with pytest.raises(BusinessError):
        x.create(ids=[uuid4()])
    assert x.w.scalar("SELECT count(*) FROM med.stock_count") == 0


@pytest.mark.parametrize("stage", ["COUNTING", "SUBMITTED", "APPROVED"])
def test_database_freeze_blocks_reservation_stock_and_cost_until_cancel(counted, stage):
    x = counted
    cid = x.create()
    if stage != "COUNTING":
        x.submit(cid)
    if stage == "APPROVED":
        x.review(cid)
    demand = x.w.demand("1", sku=x.f.sku)
    with pytest.raises(BusinessError) as exc:
        x.w.service.reserve(x.w.ctx, demand, str(uuid4()))
    assert exc.value.code == "COUNT_FROZEN"
    for sql in [
        "UPDATE med.stock_balance SET version=version+1 WHERE id=:id",
        "UPDATE med.cost_balance SET amount=amount+1 WHERE sku_id=:id",
    ]:
        with pytest.raises(DBAPIError) as exc:
            x.w.execute(sql, id=x.balance if "stock_balance" in sql else x.f.sku)
        assert exc.value.orig.sqlstate == "PZ001"
    x.service.cancel(x.f.maker, cid, x.transition(cid), str(uuid4()))
    result = x.w.service.reserve(x.w.ctx, demand, str(uuid4()))
    assert result
    assert x.w.scalar("SELECT reserved FROM med.stock_balance WHERE id=:id", id=x.balance) == 1


def test_freeze_prevents_new_location_for_the_same_subject_lot(counted):
    x = counted
    cid = x.create()
    loc = uuid4()
    x.w.execute(
        """INSERT INTO med.location(tenant_id,id,warehouse_id,code,created_by,updated_by)
        VALUES(:t,:id,:w,'NEW',:a,:a)""",
        t=x.f.maker.tenant_id,
        id=loc,
        w=x.w.warehouse,
        a=x.f.maker.principal_id,
    )
    with pytest.raises(DBAPIError) as exc:
        x.w.execute(
            """INSERT INTO med.stock_balance(tenant_id,id,entity_id,owner_id,warehouse_id,
            location_id,sku_id,lot_id,quality_status,on_hand,created_by,updated_by)
            SELECT tenant_id,:id,entity_id,owner_id,warehouse_id,:loc,sku_id,lot_id,
            quality_status,0,
            created_by,updated_by FROM med.stock_balance WHERE id=:source""",
            id=uuid4(),
            loc=loc,
            source=x.balance,
        )
    assert exc.value.orig.sqlstate == "PZ001"
    x.submit(cid, "10")
    x.review(cid)
    x.post(cid)
    assert x.w.service.reserve(x.w.ctx, x.w.demand("1", sku=x.f.sku), str(uuid4()))


def test_freeze_and_reservation_race_has_only_one_winner(counted):
    x = counted
    demand = x.w.demand("1", sku=x.f.sku)

    def attempt(kind):
        try:
            return (
                "ok",
                x.create()
                if kind == "count"
                else x.w.service.reserve(x.w.ctx, demand, str(uuid4())),
            )
        except BusinessError as exc:
            return (exc.code, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["count", "reserve"]))
    assert sum(r[0] == "ok" for r in results) == 1
    assert any(r[0] in {"COUNT_FROZEN", "COUNT_COMMITTED_STOCK"} for r in results)
