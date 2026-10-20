from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from medinv.modules.inventory.api.schemas import ReleaseRequest, ReserveRequest
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def key():
    return str(uuid4())


def test_reserve_release_and_transactional_events(world):
    request = world.demand("4")
    result = world.service.reserve(world.ctx, request, key())
    assert (
        world.scalar("SELECT reserved FROM med.stock_balance WHERE id=:id", id=world.balance) == 4
    )
    assert (
        world.scalar(
            "SELECT reserved_qty FROM med.inventory_demand_line WHERE demand_id=:id",
            id=request.source_id,
        )
        == 4
    )
    assert world.scalar("SELECT count(*) FROM med.outbox_event") == 1
    release = ReleaseRequest(expected_version="1", reason="客户取消")
    released = world.service.release(world.ctx, UUID(result["reservation_id"]), release, key())
    assert released["state"] == "RELEASED"
    assert (
        world.scalar("SELECT reserved FROM med.stock_balance WHERE id=:id", id=world.balance) == 0
    )
    assert world.scalar("SELECT count(*) FROM med.audit_event") == 2
    assert world.scalar("SELECT count(*) FROM med.outbox_delivery") == 2
    assert (
        world.service.release(world.ctx, UUID(result["reservation_id"]), release, key()) == released
    )
    assert world.scalar("SELECT count(*) FROM med.audit_event") == 2


def test_idempotency_before_versions_and_permanent_operation_key(world):
    request = world.demand("3")
    idempotency = key()
    result = world.service.reserve(world.ctx, request, idempotency)
    assert world.service.reserve(world.ctx, request, idempotency) == result
    assert world.service.reserve(world.ctx, request, key()) == result
    altered = request.model_dump(mode="json")
    altered["lines"][0]["base_qty"] = "2"
    with pytest.raises(BusinessError, match="内容不一致"):
        world.service.reserve(world.ctx, ReserveRequest.model_validate(altered), idempotency)
    with pytest.raises(BusinessError) as err:
        world.service.reserve(world.ctx, ReserveRequest.model_validate(altered), key())
    assert err.value.code == "OPERATION_CONFLICT"
    assert world.scalar("SELECT count(*) FROM med.reservation") == 1
    assert world.scalar("SELECT count(*) FROM med.outbox_event") == 1
    assert (
        world.scalar("SELECT count(*) FROM med.idempotency_record WHERE state='IN_PROGRESS'") == 0
    )


def test_multiline_shortage_rolls_back_all_facts(world):
    request = world.demand("6")
    second_line = uuid4()
    world.execute(
        """INSERT INTO med.inventory_demand_line
        (tenant_id,id,demand_id,sku_id,requested_qty) VALUES (:tenant,:id,:source,:sku,6)""",
        tenant=world.ctx.tenant_id,
        id=second_line,
        source=request.source_id,
        sku=world.sku,
    )
    body = request.model_dump(mode="json")
    body["lines"].append(
        {"source_line_id": str(second_line), "sku_id": str(world.sku), "base_qty": "6"}
    )
    with pytest.raises(BusinessError) as err:
        world.service.reserve(world.ctx, ReserveRequest.model_validate(body), key())
    assert err.value.code == "INSUFFICIENT_STOCK"
    for table in (
        "reservation",
        "reservation_allocation",
        "command_result",
        "idempotency_record",
        "audit_event",
        "outbox_event",
    ):
        assert world.scalar(f"SELECT count(*) FROM med.{table}") == 0
    assert world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 0
    assert world.scalar("SELECT sum(reserved_qty) FROM med.inventory_demand_line") == 0


def test_concurrent_reservations_cannot_oversell(world):
    requests = [world.demand("1") for _ in range(20)]
    barrier = Barrier(20)

    def reserve(req):
        barrier.wait(timeout=15)
        try:
            return world.service.reserve(world.ctx, req, key())["state"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(reserve, requests))
    assert results.count("ACTIVE") == 10, results
    assert results.count("INSUFFICIENT_STOCK") == 10, results
    assert world.scalar(
        "SELECT reserved FROM med.stock_balance WHERE id=:id", id=world.balance
    ) == Decimal(10)
    assert world.scalar("SELECT count(*) FROM med.reservation") == 10


def test_concurrent_duplicate_request_has_one_effect(world):
    request = world.demand("3")
    idempotency = key()
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(lambda _: world.service.reserve(world.ctx, request, idempotency), range(10))
        )
    assert all(result == results[0] for result in results)
    assert world.scalar("SELECT count(*) FROM med.reservation") == 1
    assert world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 3


def test_fefo_excludes_expired_quarantined_and_blocked_lots(world):
    _, early = world.add_balance("2", days=10)
    world.add_balance("100", days=-1)
    world.add_balance("100", days=1, quality="QUARANTINE")
    blocked, _ = world.add_balance("100", days=2)
    world.execute("UPDATE med.lot SET quality_blocked=true WHERE id=:id", id=blocked)
    result = world.service.reserve(world.ctx, world.demand("3"), key())
    assert [(item["balance_id"], item["base_qty"]) for item in result["allocations"]] == [
        (str(early), "2.000000"),
        (str(world.balance), "1.000000"),
    ]
    snapshot = world.service.availability(world.ctx, world.entity, world.warehouse, world.sku)
    assert sum(Decimal(item["available"]) for item in snapshot["items"]) == 9
    assert sum(not item["eligible"] for item in snapshot["items"]) == 3


@pytest.mark.parametrize(
    "change,code",
    [
        ("UPDATE med.stock_gate SET blocked=true", "SKU_BLOCKED"),
        ("UPDATE med.inventory_demand SET checks_completed=false", "SOURCE_NOT_APPROVED"),
        (
            "UPDATE med.inventory_demand SET eligible_until=now()-interval '1 day'",
            "SOURCE_NOT_APPROVED",
        ),
        ("UPDATE med.inventory_demand SET version=2", "VERSION_CONFLICT"),
        ("UPDATE med.product SET approval_state='DRAFT'", "INSUFFICIENT_STOCK"),
    ],
)
def test_source_and_quality_guards(world, change, code):
    request = world.demand("1")
    world.execute(change)
    with pytest.raises(BusinessError) as err:
        world.service.reserve(world.ctx, request, key())
    assert err.value.code == code
    assert world.scalar("SELECT count(*) FROM med.idempotency_record") == 0


def test_release_rejects_paid_protection_and_unauthorized_entity(world):
    result = world.service.reserve(world.ctx, world.demand("1"), key())
    reservation_id = UUID(result["reservation_id"])
    release = ReleaseRequest(expected_version="1", reason="取消")
    world.execute("UPDATE med.reservation SET protected_until=now()-interval '1 second'")
    with pytest.raises(BusinessError) as err:
        world.service.release(world.ctx, reservation_id, release, key())
    assert err.value.code == "PAYMENT_PROTECTED"
    with pytest.raises(BusinessError) as err:
        world.service.release(
            replace(world.ctx, entity_ids=frozenset()), reservation_id, release, key()
        )
    assert err.value.status == 404
    assert world.scalar("SELECT sum(reserved) FROM med.stock_balance") == 1


def test_inventory_options_respect_entity_scope(world):
    result = world.service.options(world.ctx, world.entity)
    assert result["warehouses"] == [{"id": str(world.warehouse), "name": "测试仓库"}]
    assert result["skus"][0]["id"] == str(world.sku)
    with pytest.raises(BusinessError):
        world.service.options(world.ctx, uuid4())
