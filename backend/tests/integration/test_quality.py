from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_procurement import flow  # noqa: F401
from test_transfers import maintenance, transferred  # noqa: F401

from medinv.modules.inventory.api.counts import CountCreate
from medinv.modules.inventory.api.quality import DispositionCreate, DispositionReview
from medinv.modules.inventory.application import quality_posting
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.inventory.application.quality import QualityService
from medinv.modules.inventory.application.service import InventoryService
from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated(transferred):  # noqa: F811
    x = transferred
    tid = x.ready()
    x.receive(tid, "10", "QUARANTINE")
    service = QualityService(x.w.uow)
    bid = x.w.scalar(
        "SELECT id FROM med.stock_balance WHERE sku_id=:s AND quality_status='QUARANTINE'",
        s=x.f.sku,
    )
    lot = x.detail(tid)["lines"][0]["lot_no"]

    def body(kind="RELEASE", qty="6", **changes):
        return DispositionCreate(
            **(
                dict(
                    client_operation_id=uuid4(),
                    entity_id=x.w.entity,
                    balance_id=bid,
                    kind=kind,
                    qty=qty,
                    lot_no=lot,
                    reason="异常到货处置",
                    evidence="复验记录 QA-2026-001",
                )
                | changes
            )
        )

    def create(kind="RELEASE", qty="6", request=None, ctx=None):
        return UUID(
            service.create(ctx or x.f.maker, request or body(kind, qty), str(uuid4()))["id"]
        )

    def detail(did):
        return service.detail(x.f.maker, did)

    def transition(did):
        return Transition(
            expected_version=detail(did)["document"]["version"], reason="实物复核与记录一致"
        )

    def review(did, ctx=None, decision="APPROVE"):
        return service.transition(
            ctx or x.f.reviewer,
            did,
            DispositionReview(**transition(did).model_dump(), decision=decision),
            str(uuid4()),
            True,
        )

    def post(did, ctx=None, request=None):
        return quality_posting.post(
            service, ctx or x.f.reviewer, did, request or transition(did), str(uuid4())
        )

    def ready(kind="RELEASE", qty="6"):
        did = create(kind, qty)
        review(did)
        return did

    def stock(quality):
        return x.w.scalar(
            "SELECT coalesce(sum(on_hand),0) FROM med.stock_balance "
            "WHERE sku_id=:s AND warehouse_id=:w AND quality_status=:q",
            s=x.f.sku,
            w=x.dest["id"],
            q=quality,
        )

    return SimpleNamespace(**locals())


def test_release_then_write_off_closes_quarantine_and_keeps_sales_and_cost_consistent(isolated):
    q = isolated
    x = q.x
    did = q.ready()
    assert q.stock("QUARANTINE") == 10  # Approval never mutates stock.
    before = x.w.scalar("SELECT count(*) FROM med.cost_ledger")
    assert q.post(did)["cost_delta"] == "0.00000000"
    assert q.stock("QUALIFIED") == 6
    assert q.stock("QUARANTINE") == 4
    assert x.w.scalar("SELECT count(*) FROM med.cost_ledger") == before
    availability = InventoryService(x.w.uow).availability(
        x.f.maker, x.w.entity, UUID(x.dest["id"]), x.f.sku
    )
    assert next(r for r in availability["items"] if r["eligible"])["available"] == "6.000000"
    loss = q.ready("WRITE_OFF", "4")
    result = q.post(loss)
    assert result["loss_cost"] == "46.01600000"
    assert q.stock("QUARANTINE") == 0
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 6
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == Decimal(
        "69.024"
    )
    assert x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert q.service.candidates(x.f.maker, x.w.entity, "", 0)["items"] == []
    assert len(q.service.listing(x.f.maker, x.w.entity, 0)["items"]) == 2
    for action, expected in [("QUALITY_RELEASE", 0), ("QUALITY_WRITE_OFF", -4)]:
        assert (
            x.w.scalar("SELECT sum(qty_delta) FROM med.stock_ledger WHERE event_type=:a", a=action)
            == expected
        )


def test_write_off_consumes_exact_final_cost_residual(isolated):
    q = isolated
    x = q.x
    x.w.execute("UPDATE med.cost_balance SET amount=1.00000001 WHERE sku_id=:s", s=x.f.sku)
    losses = [Decimal(q.post(q.ready("WRITE_OFF", qty))["loss_cost"]) for qty in ("3", "3", "4")]
    assert sum(losses) == Decimal("1.00000001")
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 0
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 0
    assert q.stock("QUARANTINE") == 0


def test_independent_review_post_cancel_and_immutable_facts(isolated):
    q = isolated
    x = q.x
    did = q.create()
    for action in (lambda: q.post(did), lambda: q.review(did, ctx=x.f.maker)):
        with pytest.raises(BusinessError):
            action()
    q.review(did)
    with pytest.raises(BusinessError) as exc:
        q.post(did, ctx=x.f.maker)
    assert exc.value.code == "MAKER_CHECKER"
    q.post(did)
    with pytest.raises(BusinessError):
        q.service.transition(x.f.maker, did, q.transition(did), str(uuid4()))
    with pytest.raises(DBAPIError):
        x.w.execute("UPDATE med.quality_disposition SET evidence='changed' WHERE id=:id", id=did)
    with pytest.raises(DBAPIError):
        x.w.execute("DELETE FROM med.quality_disposition_item")
    cancelled = q.create(qty="4")
    q.review(cancelled, decision="REJECT")
    with pytest.raises(BusinessError):
        q.post(cancelled)
    cancelled = q.ready(qty="4")
    q.service.transition(x.f.maker, cancelled, q.transition(cancelled), str(uuid4()))
    assert q.detail(cancelled)["document"]["state"] == "CANCELLED"


@pytest.mark.parametrize("change", ["gate", "lot", "expiry", "product", "validity"])
def test_release_revalidates_current_quality_write_off_keeps_global_blocks(isolated, change):
    q = isolated
    x = q.x
    did = q.ready()
    statements = {
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
        "lot": "UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s",
        "expiry": "UPDATE med.lot SET expiry_exclusive=now()-interval '1 day' WHERE sku_id=:s",
        "product": "UPDATE med.product SET approval_state='SUSPENDED' WHERE id="
        "(SELECT product_id FROM med.sku WHERE id=:s)",
        "validity": "UPDATE med.product SET valid_until=current_date-1 WHERE id="
        "(SELECT product_id FROM med.sku WHERE id=:s)",
    }
    x.w.execute(statements[change], s=x.f.sku)
    with pytest.raises(BusinessError) as exc:
        q.post(did)
    assert exc.value.code == "QUALITY_RELEASE_INELIGIBLE"
    assert q.stock("QUARANTINE") == 10
    assert q.post(q.ready("WRITE_OFF", "10"))["loss_cost"] == "115.04000000"
    if change == "gate":
        assert x.w.scalar("SELECT blocked FROM med.stock_gate WHERE sku_id=:s", s=x.f.sku)
    if change == "lot":
        assert x.w.scalar("SELECT quality_blocked FROM med.lot WHERE sku_id=:s", s=x.f.sku)


def test_unqualified_cannot_release_and_source_and_lot_and_qty_are_checked(isolated):
    q = isolated
    x = q.x
    for body, code in [
        (q.body(lot_no="other"), "QUALITY_LOT_MISMATCH"),
        (q.body(qty="11"), "INSUFFICIENT_STOCK"),
        (q.body(qty="0.1"), "QTY_SCALE_INVALID"),
        (q.body(balance_id=x.source), "QUALITY_SOURCE_INVALID"),
    ]:
        with pytest.raises(BusinessError) as exc:
            q.create(request=body)
        assert exc.value.code == code
    x.w.execute("UPDATE med.stock_balance SET quality_status='UNQUALIFIED' WHERE id=:id", id=q.bid)
    with pytest.raises(BusinessError) as exc:
        q.create()
    assert exc.value.code == "QUALITY_RELEASE_INELIGIBLE"
    assert q.post(q.ready("WRITE_OFF", "10"))["state"] == "POSTED"


def test_reserved_blocked_source_and_concurrent_documents_cannot_overconsume(isolated):
    q = isolated
    x = q.x
    first, second = q.ready(qty="6"), q.ready("WRITE_OFF", "6")
    x.w.execute("UPDATE med.stock_balance SET blocked=5 WHERE id=:id", id=q.bid)
    with pytest.raises(BusinessError) as exc:
        q.post(first)
    assert exc.value.code == "INSUFFICIENT_STOCK"
    x.w.execute("UPDATE med.stock_balance SET blocked=0 WHERE id=:id", id=q.bid)

    def attempt(did):
        try:
            return q.post(did)["state"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [first, second]))
    assert sorted(outcomes) == ["INSUFFICIENT_STOCK", "POSTED"]
    assert q.stock("QUARANTINE") == 4


def test_permanent_create_and_post_replay_survive_http_idempotency_expiry(isolated):
    q = isolated
    body = q.body()
    did = q.create(request=body)
    assert q.create(request=body) == did
    with pytest.raises(BusinessError) as exc:
        q.create(request=body.model_copy(update={"evidence": "changed"}))
    assert exc.value.code == "OPERATION_CONFLICT"
    q.review(did)
    request = q.transition(did)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: q.post(did, request=request), range(2)))
    assert results[0] == results[1]
    maintenance(q.x, "DELETE FROM med.idempotency_record")
    assert q.post(did, request=request) == results[0]
    assert q.stock("QUARANTINE") == 4


@pytest.mark.parametrize("kind", ["RELEASE", "WRITE_OFF"])
def test_count_freeze_and_closed_period_prevent_all_quality_effects(isolated, kind):
    q = isolated
    x = q.x
    did = q.ready(kind)
    counts = CountService(x.w.uow)
    cid = UUID(
        counts.create(
            x.f.maker,
            CountCreate(
                client_operation_id=uuid4(),
                entity_id=x.w.entity,
                warehouse_id=x.w.warehouse,
                balance_ids=[x.source],
                reason="同成本池盘点冻结",
            ),
            str(uuid4()),
        )["id"]
    )
    before = x.w.scalar("SELECT count(*) FROM med.posting_batch")
    with pytest.raises(BusinessError) as exc:
        q.post(did)
    assert exc.value.code == "COUNT_FROZEN"
    assert x.w.scalar("SELECT count(*) FROM med.posting_batch") == before
    assert q.stock("QUARANTINE") == 10
    counts.cancel(x.f.maker, cid, Transition(expected_version="1", reason="取消实盘"), str(uuid4()))
    maintenance(x, "UPDATE med.accounting_period SET state='CLOSED'")
    with pytest.raises(BusinessError) as exc:
        q.post(did)
    assert exc.value.code == "PERIOD_CLOSED"


@pytest.mark.parametrize("kind", ["RELEASE", "WRITE_OFF"])
def test_commit_guard_rolls_back_stock_cost_and_facts(isolated, kind):
    q = isolated
    x = q.x
    did = q.ready(kind)
    before = x.w.scalar("SELECT count(*) FROM med.stock_ledger")
    audit = x.w.scalar("SELECT count(*) FROM med.audit_event")

    def fail():
        raise BusinessError("TEST_REVOKED", "授权失效", 403)

    marker = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            q.post(did)
    finally:
        before_business_commit.reset(marker)
    assert x.w.scalar("SELECT count(*) FROM med.stock_ledger") == before
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == audit
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == Decimal(
        "115.04"
    )
    assert q.stock("QUARANTINE") == 10
    assert q.detail(did)["document"]["state"] == "APPROVED"


def test_tenant_entity_role_scope_and_ownership(isolated):
    q = isolated
    x = q.x
    did = q.create()
    for ctx in [replace(x.f.maker, tenant_id=uuid4()), replace(x.f.maker, entity_ids=frozenset())]:
        with pytest.raises(BusinessError):
            q.service.detail(ctx, did)
        with pytest.raises(BusinessError):
            q.create(ctx=ctx)
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert q.service.detail(viewer, did)["document"]["id"] == str(did)
    with pytest.raises(BusinessError):
        q.create(ctx=viewer)
    with pytest.raises(BusinessError):
        q.review(did, ctx=replace(x.f.reviewer, permissions=ROLE_PERMISSIONS["OPERATOR"]))
    assert q.service.candidates(x.f.maker, x.w.entity, "not present", 0)["items"] == []
