from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_transfers import maintenance
from test_transfers import transferred as transferred

from medinv.modules.inventory.api.counts import CountCreate
from medinv.modules.inventory.api.transfer_losses import TransferLossCreate, TransferLossReview
from medinv.modules.inventory.api.transfers import TransferReceive
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.inventory.application.transfer_losses import TransferLossService
from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def losses(transferred):
    x = transferred
    service = TransferLossService(x.service)
    draft = x.create("1")
    tid = x.ready()

    def body(qty="2", transfer=None, **changes):
        transfer = transfer or tid
        line = x.detail(transfer)["lines"][0]
        return TransferLossCreate(
            **(
                dict(
                    client_operation_id=uuid4(),
                    expected_version=x.detail(transfer)["document"]["version"],
                    line_id=line["id"],
                    qty=qty,
                    lot_no=line["lot_no"],
                    reason="运输短少",
                    evidence="承运核查记录 LOSS-001",
                )
                | changes
            )
        )

    def create(qty="2", request=None, ctx=None, transfer=None):
        return UUID(
            service.create(
                ctx or x.f.maker, transfer or tid, request or body(qty, transfer), str(uuid4())
            )["id"]
        )

    def detail(lid):
        return service.detail(x.f.maker, tid, lid)

    def transition(lid):
        return Transition(expected_version=detail(lid)["version"], reason="已核对运输差异记录")

    def review(lid, ctx=None, decision="APPROVE"):
        return service.transition(
            ctx or x.f.reviewer,
            tid,
            lid,
            TransferLossReview(**transition(lid).model_dump(), decision=decision),
            str(uuid4()),
            True,
        )

    def post(lid, ctx=None, request=None):
        return service.post(ctx or x.f.reviewer, tid, lid, request or transition(lid), str(uuid4()))

    def cancel(lid):
        return service.transition(x.f.maker, tid, lid, transition(lid), str(uuid4()))

    def ready(qty="2"):
        lid = create(qty)
        review(lid)
        return lid

    return SimpleNamespace(**locals())


@pytest.mark.parametrize("loss_first", [True, False])
def test_receipt_and_loss_both_orders_close_with_real_quantities_and_cost(losses, loss_first):
    q = losses
    x = q.x
    if not loss_first:
        x.receive(q.tid, "8")
    lid = q.ready()
    before = x.w.scalar("SELECT count(*) FROM med.cost_ledger")
    result = q.post(lid)
    assert result["loss_cost"] == "23.00800000"
    assert result["transfer_state"] == ("IN_TRANSIT" if loss_first else "CLOSED_WITH_LOSS")
    if loss_first:
        assert x.receive(q.tid, "8")["state"] == "CLOSED_WITH_LOSS"
    doc = x.detail(q.tid)
    line = doc["lines"][0]
    assert line["received_qty"] == "8.000000"
    assert line["lost_qty"] == "2.000000"
    assert line["remaining_qty"] == "0.000000"
    assert doc["document"]["state"] == "CLOSED_WITH_LOSS"
    assert x.stock(doc["document"]["transit_warehouse_id"]) == 0
    assert x.stock(x.dest["id"]) == 8
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == Decimal(
        "92.032"
    )
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 8
    assert x.w.scalar("SELECT count(*) FROM med.cost_ledger") == before + 1
    assert x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0
    assert (
        q.service.listing(x.f.maker, q.tid, 0)["items"][0]["result"]["posting_id"]
        == result["posting_id"]
    )
    # A resolved transit pool no longer blocks the existing count workflow.
    counts = CountService(x.w.uow)
    assert (
        counts.create(
            x.f.maker,
            CountCreate(
                client_operation_id=uuid4(),
                entity_id=x.w.entity,
                warehouse_id=x.w.warehouse,
                balance_ids=[x.source],
                reason="调拨结清后盘点",
            ),
            str(uuid4()),
        )["state"]
        == "COUNTING"
    )


@pytest.mark.parametrize("amount", ["1.00000001", "0"])
def test_partial_losses_consume_final_cost_residual_without_fake_receipts(losses, amount):
    q = losses
    x = q.x
    x.w.execute(
        "UPDATE med.cost_balance SET amount=:a WHERE sku_id=:s", a=Decimal(amount), s=x.f.sku
    )
    values = [Decimal(q.post(q.ready(qty))["loss_cost"]) for qty in ("3", "3", "4")]
    assert sum(values) == Decimal(amount)
    assert x.detail(q.tid)["document"]["state"] == "CLOSED_WITH_LOSS"
    assert x.detail(q.tid)["lines"][0]["received_qty"] == "0"
    assert x.detail(q.tid)["receipts"] == []
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 0
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 0
    assert (
        x.w.scalar("SELECT sum(qty_delta) FROM med.stock_ledger WHERE event_type='TRANSFER_LOSS'")
        == -10
    )


def test_loss_requires_independent_review_posting_and_immutable_application(losses):
    q = losses
    x = q.x
    lid = q.create()
    with pytest.raises(BusinessError) as exc:
        q.post(lid)
    assert exc.value.code == "TRANSFER_LOSS_NOT_APPROVED"
    with pytest.raises(BusinessError) as exc:
        q.review(lid, ctx=x.f.maker)
    assert exc.value.code == "MAKER_CHECKER"
    with pytest.raises(DBAPIError):
        x.w.execute("UPDATE med.transfer_loss SET qty=1 WHERE id=:id", id=lid)
    q.review(lid)
    with pytest.raises(BusinessError) as exc:
        q.post(lid, ctx=x.f.maker)
    assert exc.value.code == "MAKER_CHECKER"
    q.post(lid)
    with pytest.raises(BusinessError):
        q.cancel(lid)
    for sql in [
        "DELETE FROM med.transfer_loss WHERE id=:id",
        "UPDATE med.transfer_loss SET state='APPROVED' WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            x.w.execute(sql, id=lid)


def test_one_pending_application_per_line_reject_cancel_then_reapply(losses):
    q = losses
    lid = q.create()
    with pytest.raises(BusinessError) as exc:
        q.create()
    assert exc.value.code == "TRANSFER_LOSS_PENDING"
    q.review(lid, decision="REJECT")
    assert q.detail(lid)["state"] == "CANCELLED"
    lid = q.ready()
    q.cancel(lid)
    assert q.ready() != lid
    assert q.x.detail(q.tid)["lines"][0]["remaining_qty"] == "10.000000"


def test_invalid_lot_foreign_line_scale_excess_and_unsent_transfer(losses):
    q = losses
    for request, code in [
        (q.body(qty="11"), "TRANSFER_OVER_LOSS"),
        (q.body(qty="0.1"), "QTY_SCALE_INVALID"),
        (q.body(lot_no="wrong"), "TRANSFER_LOT_MISMATCH"),
        (q.body(line_id=uuid4()), "TRANSFER_LINES"),
    ]:
        with pytest.raises(BusinessError) as exc:
            q.create(request=request)
        assert exc.value.code == code
    tid = q.draft
    with pytest.raises(BusinessError) as exc:
        q.create(transfer=tid)
    assert exc.value.code == "TRANSFER_LOSS_STATE"


@pytest.mark.parametrize("approved", [True, False])
def test_application_does_not_reserve_and_can_be_cancelled_after_late_receipt(losses, approved):
    q = losses
    lid = q.create("8")
    if approved:
        q.review(lid)
    q.x.receive(q.tid, "10")
    with pytest.raises(BusinessError) as exc:
        q.post(lid) if approved else q.review(lid)
    assert exc.value.code == "TRANSFER_LOSS_STATE"
    assert q.cancel(lid)["state"] == "CANCELLED"
    assert q.x.detail(q.tid)["document"]["state"] == "RECEIVED"
    assert q.x.w.scalar(
        "SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=q.x.f.sku
    ) == Decimal("115.04")


def test_partial_late_receipt_invalidates_excess_approved_loss_and_posted_loss_limits_receipt(
    losses,
):
    q = losses
    lid = q.ready("8")
    q.x.receive(q.tid, "4")
    with pytest.raises(BusinessError) as exc:
        q.post(lid)
    assert exc.value.code == "TRANSFER_OVER_LOSS"
    q.cancel(lid)
    q.post(q.ready("2"))
    with pytest.raises(BusinessError) as exc:
        q.x.receive(q.tid, "5")
    assert exc.value.code == "TRANSFER_OVER_RECEIVE"
    assert q.x.receive(q.tid, "4")["state"] == "CLOSED_WITH_LOSS"


def test_receipt_and_loss_race_cannot_consume_same_transit_twice(losses):
    q = losses
    lid = q.ready("8")
    receipt = q.x.receive_body(q.tid, "8")
    posting = q.transition(lid)

    def attempt(kind):
        try:
            return (
                q.post(lid, request=posting)
                if kind == "loss"
                else q.x.receive(q.tid, request=receipt)
            )
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["loss", "receipt"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    line = q.x.detail(q.tid)["lines"][0]
    assert Decimal(line["received_qty"]) + Decimal(line["lost_qty"]) == 8
    assert line["remaining_qty"] == "2.000000"


def test_create_and_concurrent_post_replay_survive_idempotency_expiry(losses):
    q = losses
    body = q.body()
    lid = q.create(request=body)
    assert q.create(request=body) == lid
    with pytest.raises(BusinessError) as exc:
        q.create(request=body.model_copy(update={"evidence": "changed"}))
    assert exc.value.code == "OPERATION_CONFLICT"
    q.review(lid)
    body = q.transition(lid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: q.post(lid, request=body), range(2)))
    assert results[0] == results[1]
    maintenance(q.x, "DELETE FROM med.idempotency_record")
    assert q.post(lid, request=body) == results[0]
    assert (
        q.x.w.scalar("SELECT count(*) FROM med.stock_ledger WHERE event_type='TRANSFER_LOSS'") == 1
    )


@pytest.mark.parametrize("change", ["gate", "lot", "expiry", "product"])
def test_loss_can_close_abnormal_transit_without_clearing_quality_holds(losses, change):
    q = losses
    x = q.x
    lid = q.ready("10")
    sql = {
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
        "lot": "UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s",
        "expiry": "UPDATE med.lot SET expiry_exclusive=now()-interval '1 day' WHERE sku_id=:s",
        "product": "UPDATE med.product SET approval_state='SUSPENDED' WHERE id="
        "(SELECT product_id FROM med.sku WHERE id=:s)",
    }
    x.w.execute(sql[change], s=x.f.sku)
    assert q.post(lid)["transfer_state"] == "CLOSED_WITH_LOSS"
    if change == "gate":
        assert x.w.scalar("SELECT blocked FROM med.stock_gate WHERE sku_id=:s", s=x.f.sku)
    if change == "lot":
        assert x.w.scalar("SELECT quality_blocked FROM med.lot WHERE sku_id=:s", s=x.f.sku)


def test_closed_period_and_commit_guard_leave_no_partial_loss(losses):
    q = losses
    x = q.x
    lid = q.ready()
    before = x.w.scalar("SELECT count(*) FROM med.stock_ledger")
    version = x.detail(q.tid)["document"]["version"]
    audit = x.w.scalar("SELECT count(*) FROM med.audit_event")

    def fail():
        raise BusinessError("TEST_REVOKED", "授权失效", 403)

    marker = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            q.post(lid)
    finally:
        before_business_commit.reset(marker)
    assert x.w.scalar("SELECT count(*) FROM med.stock_ledger") == before
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == audit
    assert x.detail(q.tid)["document"]["version"] == version
    assert q.detail(lid)["state"] == "APPROVED"
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 10
    maintenance(x, "UPDATE med.accounting_period SET state='CLOSED'")
    with pytest.raises(BusinessError) as exc:
        q.post(lid)
    assert exc.value.code == "PERIOD_CLOSED"


def test_role_tenant_entity_and_nested_transfer_scope(losses):
    q = losses
    x = q.x
    lid = q.create()
    for ctx in [replace(x.f.maker, tenant_id=uuid4()), replace(x.f.maker, entity_ids=frozenset())]:
        with pytest.raises(BusinessError):
            q.service.detail(ctx, q.tid, lid)
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert q.service.detail(viewer, q.tid, lid)["id"] == str(lid)
    with pytest.raises(BusinessError):
        q.create(ctx=viewer)
    with pytest.raises(BusinessError):
        q.review(lid, ctx=replace(x.f.reviewer, permissions=ROLE_PERMISSIONS["OPERATOR"]))
    other = q.draft
    with pytest.raises(BusinessError):
        q.service.detail(x.f.maker, other, lid)
    with pytest.raises(BusinessError):
        q.service.post(x.f.reviewer, other, lid, q.transition(lid), str(uuid4()))


def test_two_source_lines_sharing_transit_balance_keep_separate_loss_allowances(transferred):
    x = transferred
    location, source = uuid4(), uuid4()
    x.w.execute(
        """INSERT INTO med.location(tenant_id,id,warehouse_id,code,created_by,updated_by)
      VALUES(:t,:id,:w,'L2',:a,:a)""",
        t=x.f.maker.tenant_id,
        id=location,
        w=x.w.warehouse,
        a=x.f.maker.principal_id,
    )
    x.w.execute("UPDATE med.stock_balance SET on_hand=3 WHERE id=:id", id=x.source)
    x.w.execute(
        """INSERT INTO med.stock_balance(tenant_id,id,entity_id,owner_id,warehouse_id,
      location_id,sku_id,lot_id,quality_status,on_hand,created_by,updated_by)
      SELECT tenant_id,:id,entity_id,owner_id,warehouse_id,:loc,sku_id,lot_id,quality_status,7,
      created_by,updated_by FROM med.stock_balance WHERE id=:source""",
        id=source,
        loc=location,
        source=x.source,
    )
    tid = x.create(
        request=x.body(
            lines=[{"balance_id": x.source, "qty": "3"}, {"balance_id": source, "qty": "7"}]
        )
    )
    x.review(tid)
    x.dispatch(tid)
    lines = x.detail(tid)["lines"]
    service = TransferLossService(x.service)
    body = TransferLossCreate(
        **x.transition(tid).model_dump(),
        client_operation_id=uuid4(),
        line_id=lines[0]["id"],
        qty="4",
        lot_no=lines[0]["lot_no"],
        evidence="仅首行损失",
    )
    with pytest.raises(BusinessError) as exc:
        service.create(x.f.maker, tid, body, str(uuid4()))
    assert exc.value.code == "TRANSFER_OVER_LOSS"
    body = body.model_copy(update={"qty": "3"})
    lid = UUID(service.create(x.f.maker, tid, body, str(uuid4()))["id"])
    service.transition(
        x.f.reviewer,
        tid,
        lid,
        TransferLossReview(expected_version="1", decision="APPROVE", reason="独立核对"),
        str(uuid4()),
        True,
    )
    service.post(
        x.f.reviewer, tid, lid, Transition(expected_version="2", reason="损失核销"), str(uuid4())
    )
    receipt = TransferReceive(
        **x.transition(tid).model_dump(),
        client_operation_id=uuid4(),
        lines=[
            {
                "line_id": lines[1]["id"],
                "qty": "7",
                "lot_no": lines[1]["lot_no"],
                "quality_status": "QUALIFIED",
            }
        ],
    )
    assert x.receive(tid, request=receipt)["state"] == "CLOSED_WITH_LOSS"
    assert [r["received_qty"] for r in x.detail(tid)["lines"]] == ["0", "7.000000"]
    assert [r["lost_qty"] for r in x.detail(tid)["lines"]] == ["3.000000", "0"]
