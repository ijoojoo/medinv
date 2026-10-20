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

from medinv.modules.inventory.api.counts import CountCreate
from medinv.modules.inventory.api.transfers import (
    TransferCreate,
    TransferReceive,
    TransferReview,
    WarehouseCreate,
)
from medinv.modules.inventory.application import transfer_posting
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.inventory.application.service import InventoryService
from medinv.modules.inventory.application.transfers import TransferService
from medinv.modules.procurement.api.schemas import Transition
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def maintenance(x, sql):
    owner = create_engine(os.environ["MED_MIGRATION_URL"], hide_parameters=True)
    try:
        with owner.begin() as connection:
            connection.execute(text("SET ROLE med_owner"))
            connection.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(x.f.maker.tenant_id)}
            )
            connection.execute(text(sql))
    finally:
        owner.dispose()


@pytest.fixture
def transferred(flow):
    f = flow
    w = f.world
    f.post(f.inspect(f.receive()["id"]))
    service = TransferService(w.uow)
    source = w.scalar("SELECT id FROM med.stock_balance WHERE sku_id=:s", s=f.sku)
    dest = service.create_warehouse(
        f.maker,
        WarehouseCreate(
            client_operation_id=uuid4(),
            entity_id=w.entity,
            code="DEST",
            name="调入仓",
            location_code="A1",
        ),
        str(uuid4()),
    )

    def body(qty="10", **overrides):
        data = dict(
            client_operation_id=uuid4(),
            entity_id=w.entity,
            source_warehouse_id=w.warehouse,
            destination_warehouse_id=dest["id"],
            destination_location_id=dest["location_id"],
            reason="门店补货",
            lines=[{"balance_id": source, "qty": qty}],
        )
        return TransferCreate(**(data | overrides))

    def create(qty="10", request=None, ctx=None):
        return UUID(service.create(ctx or f.maker, request or body(qty), str(uuid4()))["id"])

    def detail(tid):
        return service.detail(f.maker, tid)

    def transition(tid):
        return Transition(
            expected_version=detail(tid)["document"]["version"], reason="核对调拨实物"
        )

    def review(tid, ctx=None, decision="APPROVE"):
        return service.transition(
            ctx or f.reviewer,
            tid,
            TransferReview(**transition(tid).model_dump(), decision=decision),
            str(uuid4()),
            True,
        )

    def dispatch(tid, request=None):
        return transfer_posting.dispatch(
            service, f.maker, tid, request or transition(tid), str(uuid4())
        )

    def receive_body(tid, qty="8", quality="QUALIFIED"):
        line = detail(tid)["lines"][0]
        return TransferReceive(
            **transition(tid).model_dump(),
            client_operation_id=uuid4(),
            lines=[
                {
                    "line_id": line["id"],
                    "qty": qty,
                    "lot_no": line["lot_no"],
                    "quality_status": quality,
                }
            ],
        )

    def receive(tid, qty="8", quality="QUALIFIED", request=None):
        return transfer_posting.receive(
            service, f.maker, tid, request or receive_body(tid, qty, quality), str(uuid4())
        )

    def ready(qty="10"):
        tid = create(qty)
        review(tid)
        dispatch(tid)
        return tid

    def stock(warehouse):
        return w.scalar(
            "SELECT coalesce(sum(on_hand),0) FROM med.stock_balance "
            "WHERE warehouse_id=:w AND sku_id=:s",
            w=warehouse,
            s=f.sku,
        )

    return SimpleNamespace(**locals())


def test_transfer_partial_receipt_preserves_total_cost_and_double_ledger(transferred):
    x = transferred
    pool_before = x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku)
    tid = x.ready()
    doc = x.detail(tid)["document"]
    assert x.stock(x.w.warehouse) == 0
    assert x.stock(doc["transit_warehouse_id"]) == 10
    available = InventoryService(x.w.uow).availability(
        x.f.maker, x.w.entity, UUID(doc["transit_warehouse_id"]), x.f.sku
    )
    assert available["items"][0]["available"] == "0.000000"
    assert "IN_TRANSIT" in available["items"][0]["ineligibility_codes"]
    result = x.receive(tid)
    assert result["state"] == "PART_RECEIVED"
    assert x.stock(x.dest["id"]) == 8
    assert x.stock(doc["transit_warehouse_id"]) == 2
    assert x.detail(tid)["lines"][0]["received_qty"] == "8.000000"
    assert x.receive(tid, "2")["state"] == "RECEIVED"
    assert len(x.detail(tid)["receipts"]) == 2
    assert x.stock(doc["transit_warehouse_id"]) == 0
    assert x.stock(x.dest["id"]) == 10
    assert (
        x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == pool_before
    )
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 10
    assert (
        x.w.scalar(
            """SELECT sum(qty_delta) FROM med.stock_ledger sl JOIN med.posting_line pl
      ON (pl.tenant_id,pl.id)=(sl.tenant_id,sl.posting_line_id) JOIN med.posting_batch p
      ON (p.tenant_id,p.id)=(pl.tenant_id,pl.posting_id) WHERE p.source_id=:id""",
            id=tid,
        )
        == 0
    )
    assert (
        x.w.scalar(
            """SELECT count(*) FROM med.cost_ledger cl JOIN med.posting_line pl
      ON (pl.tenant_id,pl.id)=(cl.tenant_id,cl.posting_line_id) JOIN med.posting_batch p
      ON (p.tenant_id,p.id)=(pl.tenant_id,pl.posting_id) WHERE p.source_id=:id""",
            id=tid,
        )
        == 0
    )
    assert x.w.scalar("SELECT count(*) FROM med.payable_obligation") == 1
    assert x.w.scalar("SELECT count(*) FROM med.receivable_obligation") == 0


def test_dispatch_requires_independent_approval_and_revalidates_unreserved_stock(transferred):
    x = transferred
    tid = x.create()
    with pytest.raises(BusinessError) as exc:
        x.dispatch(tid)
    assert exc.value.code == "TRANSFER_NOT_APPROVED"
    with pytest.raises(BusinessError) as exc:
        x.review(tid, ctx=x.f.maker)
    assert exc.value.code == "MAKER_CHECKER"
    x.review(tid)
    x.w.execute("UPDATE med.stock_balance SET reserved=1 WHERE id=:b", b=x.source)
    with pytest.raises(BusinessError) as exc:
        x.dispatch(tid)
    assert exc.value.code == "INSUFFICIENT_STOCK"
    assert x.stock(x.w.warehouse) == 10
    assert x.detail(tid)["document"]["state"] == "APPROVED"
    assert x.w.scalar("SELECT count(*) FROM med.warehouse WHERE kind='TRANSIT'") == 0


@pytest.mark.parametrize("change", ["gate", "lot", "expiry", "product"])
@pytest.mark.parametrize("stage", ["dispatch", "receive"])
def test_quality_change_blocks_dispatch_and_qualified_receipt_allows_quarantine(
    transferred, change, stage
):
    x = transferred
    tid = x.create()
    x.review(tid)
    if stage == "receive":
        x.dispatch(tid)
    statements = {
        "gate": "UPDATE med.stock_gate SET blocked=true WHERE sku_id=:s",
        "lot": "UPDATE med.lot SET quality_blocked=true WHERE sku_id=:s",
        "expiry": "UPDATE med.lot SET expiry_exclusive=now()-interval '1 day' WHERE sku_id=:s",
        "product": "UPDATE med.product SET approval_state='SUSPENDED' WHERE id="
        "(SELECT product_id FROM med.sku WHERE id=:s)",
    }
    x.w.execute(statements[change], s=x.f.sku)
    with pytest.raises(BusinessError) as exc:
        getattr(x, stage)(tid)
    assert exc.value.code == "TRANSFER_INELIGIBLE"
    if stage == "dispatch":
        assert x.stock(x.w.warehouse) == 10
        return
    assert x.receive(tid, "10", "QUARANTINE")["state"] == "RECEIVED"
    assert (
        x.w.scalar(
            "SELECT on_hand FROM med.stock_balance WHERE sku_id=:s "
            "AND warehouse_id=:w AND quality_status='QUARANTINE'",
            s=x.f.sku,
            w=x.dest["id"],
        )
        == 10
    )


def test_receipt_overage_wrong_lot_and_wrong_document_reject_without_effects(transferred):
    x = transferred
    tid = x.ready()
    for qty in ("11", "0.1"):
        with pytest.raises(BusinessError) as exc:
            x.receive(tid, qty)
        assert exc.value.code in ("TRANSFER_OVER_RECEIVE", "QTY_SCALE_INVALID")
    for field, value, code in [
        ("lot_no", "wrong", "TRANSFER_LOT_MISMATCH"),
        ("line_id", uuid4(), "TRANSFER_LINES"),
    ]:
        body = x.receive_body(tid)
        body.lines[0] = body.lines[0].model_copy(update={field: value})
        with pytest.raises(BusinessError) as exc:
            x.receive(tid, request=body)
        assert exc.value.code == code
    assert x.stock(x.dest["id"]) == 0
    assert len(x.detail(tid)["receipts"]) == 0


def test_create_and_receipt_permanent_retries_and_concurrent_dispatch(transferred):
    x = transferred
    body = x.body()
    tid = x.create(request=body)
    assert x.create(request=body) == tid
    with pytest.raises(BusinessError) as exc:
        x.create(request=body.model_copy(update={"reason": "changed"}))
    assert exc.value.code == "OPERATION_CONFLICT"
    x.review(tid)
    transition = x.transition(tid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: x.dispatch(tid, request=transition), range(2)))
    assert results[0] == results[1]
    receipt = x.receive_body(tid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: x.receive(tid, request=receipt), range(2)))
    assert results[0] == results[1]
    maintenance(x, "DELETE FROM med.idempotency_record")
    assert x.receive(tid, request=receipt) == results[0]
    assert x.stock(x.dest["id"]) == 8
    assert len(x.detail(tid)["receipts"]) == 1


def test_separate_receipt_operations_with_same_version_cannot_double_receive(transferred):
    x = transferred
    tid = x.ready()
    bodies = [x.receive_body(tid, "6"), x.receive_body(tid, "6")]

    def receive(body):
        try:
            return x.receive(tid, request=body)["state"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(receive, bodies))
    assert results.count("PART_RECEIVED") == 1
    assert x.stock(x.dest["id"]) == 6


def test_transit_is_isolated_per_document(transferred):
    x = transferred
    first, second = x.ready("4"), x.ready("6")
    assert (
        x.detail(first)["document"]["transit_location_id"]
        != x.detail(second)["document"]["transit_location_id"]
    )
    with pytest.raises(BusinessError) as exc:
        x.receive(first, "5")
    assert exc.value.code == "TRANSFER_OVER_RECEIVE"
    x.receive(first, "4")
    assert x.detail(second)["lines"][0]["received_qty"] == "0"
    assert x.receive(second, "6")["state"] == "RECEIVED"


def test_count_freeze_blocks_dispatch_and_in_transit_blocks_count(transferred):
    x = transferred
    counts = CountService(x.w.uow)

    def count():
        return counts.create(
            x.f.maker,
            CountCreate(
                client_operation_id=uuid4(),
                entity_id=x.w.entity,
                warehouse_id=x.w.warehouse,
                balance_ids=[x.source],
                reason="实盘",
            ),
            str(uuid4()),
        )

    tid = x.create()
    x.review(tid)
    cid = UUID(count()["id"])
    with pytest.raises(BusinessError) as exc:
        x.dispatch(tid)
    assert exc.value.code == "COUNT_FROZEN"
    counts.cancel(x.f.maker, cid, Transition(expected_version="1", reason="先调拨"), str(uuid4()))
    x.dispatch(tid)
    with pytest.raises(BusinessError) as exc:
        count()
    assert exc.value.code == "COUNT_IN_TRANSIT"
    x.receive(tid, "10")
    assert count()["state"] == "COUNTING"


@pytest.mark.parametrize("stage", ["dispatch", "receive"])
def test_commit_guard_rolls_back_transfer_all_effects(transferred, stage):
    x = transferred
    tid = x.create()
    x.review(tid)
    if stage == "receive":
        x.dispatch(tid)
    before = x.w.scalar("SELECT count(*) FROM med.stock_ledger")
    audit = x.w.scalar("SELECT count(*) FROM med.audit_event")

    def fail():
        raise BusinessError("TEST_REVOKED", "授权失效", 403)

    marker = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            getattr(x, stage)(tid)
    finally:
        before_business_commit.reset(marker)
    assert x.w.scalar("SELECT count(*) FROM med.stock_ledger") == before
    assert x.w.scalar("SELECT count(*) FROM med.audit_event") == audit
    assert x.stock(x.dest["id"]) == 0


def test_permissions_rls_rejection_cancel_and_immutable_facts(transferred):
    x = transferred
    tid = x.create()
    for actor in [
        replace(x.f.maker, tenant_id=uuid4()),
        replace(x.f.maker, entity_ids=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            x.service.detail(actor, tid)
    viewer = replace(x.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert x.service.detail(viewer, tid)["document"]["id"] == str(tid)
    with pytest.raises(BusinessError):
        x.create(ctx=viewer)
    x.review(tid, decision="REJECT")
    with pytest.raises(BusinessError):
        x.dispatch(tid)
    tid = x.ready()
    x.receive(tid, "10")
    with pytest.raises(BusinessError):
        x.service.transition(x.f.maker, tid, x.transition(tid), str(uuid4()))
    for table in ("transfer_line", "transfer_receipt", "transfer_receipt_line"):
        with pytest.raises(DBAPIError):
            x.w.execute(f"DELETE FROM med.{table}")


def test_same_warehouse_cross_entity_and_location_mismatch_rejected(transferred):
    x = transferred
    with pytest.raises(BusinessError) as exc:
        x.create(request=x.body(destination_warehouse_id=x.w.warehouse))
    assert exc.value.code == "SAME_WAREHOUSE"
    with pytest.raises(BusinessError):
        x.create(request=x.body(destination_location_id=x.w.location))
    entity = uuid4()
    x.w.execute(
        """INSERT INTO med.legal_entity(tenant_id,id,created_by,updated_by,name,credit_code,status)
      VALUES(:t,:id,:a,:a,'另一主体','other','ACTIVE')""",
        t=x.w.ctx.tenant_id,
        id=entity,
        a=x.f.maker.principal_id,
    )
    x.w.execute("UPDATE med.warehouse SET entity_id=:e WHERE id=:id", e=entity, id=x.dest["id"])
    with pytest.raises(BusinessError) as exc:
        x.create()
    assert exc.value.code == "CROSS_ENTITY_TRANSFER_FORBIDDEN"


def test_closed_period_rejects_dispatch_and_no_cost_pool_mutation(transferred):
    x = transferred
    tid = x.create()
    x.review(tid)
    maintenance(x, "UPDATE med.accounting_period SET state='CLOSED'")
    with pytest.raises(BusinessError) as exc:
        x.dispatch(tid)
    assert exc.value.code == "PERIOD_CLOSED"
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == Decimal(10)


def test_same_lot_from_two_locations_merges_in_transit_without_losing_line_remainders(transferred):
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
    receipt = TransferReceive(
        **x.transition(tid).model_dump(),
        client_operation_id=uuid4(),
        lines=[
            {
                "line_id": line["id"],
                "qty": "3",
                "lot_no": line["lot_no"],
                "quality_status": "QUALIFIED",
            }
            for line in lines
        ],
    )
    assert x.receive(tid, request=receipt)["state"] == "PART_RECEIVED"
    assert x.stock(x.dest["id"]) == 6
    assert [r["remaining_qty"] for r in x.detail(tid)["lines"]] == ["0.000000", "4.000000"]
    receipt = TransferReceive(
        **x.transition(tid).model_dump(),
        client_operation_id=uuid4(),
        lines=[
            {
                "line_id": lines[1]["id"],
                "qty": "4",
                "lot_no": lines[1]["lot_no"],
                "quality_status": "QUALIFIED",
            }
        ],
    )
    assert x.receive(tid, request=receipt)["state"] == "RECEIVED"
    assert x.stock(x.dest["id"]) == 10
    assert x.w.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == Decimal(
        "115.04"
    )


def test_warehouse_operation_replay_and_permissions(transferred):
    x = transferred
    body = WarehouseCreate(
        client_operation_id=uuid4(),
        entity_id=x.w.entity,
        code="NEW",
        name="新增仓",
        location_code="N1",
    )
    first = x.service.create_warehouse(x.f.maker, body, str(uuid4()))
    assert x.service.create_warehouse(x.f.maker, body, str(uuid4())) == first
    for changed, code in [
        (body.model_copy(update={"name": "改名"}), "OPERATION_CONFLICT"),
        (body.model_copy(update={"client_operation_id": uuid4()}), "WAREHOUSE_CODE_EXISTS"),
        (
            body.model_copy(update={"client_operation_id": uuid4(), "code": "SYS-TRANSIT"}),
            "WAREHOUSE_CODE_RESERVED",
        ),
    ]:
        with pytest.raises(BusinessError) as exc:
            x.service.create_warehouse(x.f.maker, changed, str(uuid4()))
        assert exc.value.code == code
    actor = replace(x.f.maker, permissions=ROLE_PERMISSIONS["OPERATOR"])
    with pytest.raises(BusinessError):
        x.service.create_warehouse(actor, body, str(uuid4()))
