from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_transfers import transferred as transferred

from medinv.modules.inventory.api.transfers import WarehouseCreate
from medinv.modules.inventory.api.warehouses import LocationCreate, WarehouseRename
from medinv.modules.inventory.application.warehouses import WarehouseService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_archived_warehouse_hidden_from_choices_but_history_remains(flow):
    f = flow
    w = f.world
    before = snapshot(f)
    w.execute("UPDATE med.warehouse SET archived_at=now() WHERE id=:id", id=w.warehouse)
    assert not any(
        r["id"] == str(w.warehouse) for r in f.service.options(f.maker, w.entity)["warehouses"]
    )
    service = WarehouseService(w.uow)
    assert service.listing(f.maker, w.entity, "", 0)["items"] == []
    assert service.detail(f.maker, w.warehouse)["warehouse"]["archived_at"]
    assert snapshot(f) == before


def body(service, f, target=None, **overrides):
    target = target or f.world.warehouse
    return LocationCreate(
        **(
            {
                "client_operation_id": uuid4(),
                "expected_version": service.detail(f.maker, target)["warehouse"]["version"],
                "code": "A-02",
                "name": "常温二号位",
                "reason": "新增收货区",
            }
            | overrides
        )
    )


def snapshot(f):
    return [
        f.world.scalar(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id),'[]') FROM med.{table} t"
        )
        for table in ("stock_balance", "stock_ledger", "cost_balance", "cost_ledger")
    ]


def test_new_location_procurement_posting_and_rename_preserve_stock_and_cost(flow):
    f = flow
    w = f.world
    service = WarehouseService(w.uow)
    loc = service.add_location(f.maker, w.warehouse, body(service, f), str(uuid4()))
    assert loc["warehouse_version"] == "2"
    assert any(
        location["id"] == loc["id"] and location["name"] == "常温二号位"
        for location in f.service.options(f.maker, w.entity)["locations"]
    )
    receipt = f.receive(
        extra=[
            {
                "order_line_id": f.detail()["lines"][0]["id"],
                "location_id": loc["id"],
                "qty": "10",
                "lot_no": "NEW-LOCATION",
                "mfg_date": f.day - timedelta(days=1),
                "expiry_date": f.day + timedelta(days=365),
            }
        ]
    )
    f.post(f.inspect(receipt["id"]))
    assert (
        w.scalar("SELECT on_hand FROM med.stock_balance WHERE location_id=:id", id=loc["id"]) == 10
    )
    before = snapshot(f)
    service.rename(
        f.maker,
        w.warehouse,
        WarehouseRename(expected_version="2", name="中心仓", reason="统一命名"),
        str(uuid4()),
    )
    service.rename(
        f.maker,
        w.warehouse,
        WarehouseRename(expected_version="1", name="收货一组", reason="更正名称"),
        str(uuid4()),
        UUID(loc["id"]),
    )
    assert snapshot(f) == before
    detail = service.detail(f.maker, w.warehouse)
    assert detail["warehouse"]["code"] == "WH1"
    assert detail["warehouse"]["version"] == "4"
    renamed = next(location for location in detail["locations"] if location["id"] == loc["id"])
    assert (renamed["code"], renamed["name"], renamed["version"]) == ("A-02", "收货一组", "2")
    assert detail["history"][0]["detail"]["before"] == "常温二号位"
    assert detail["history"][0]["detail"]["after"] == "收货一组"
    with pytest.raises(DBAPIError):
        w.execute(
            "UPDATE med.audit_event SET reason='tamper' WHERE resource_id=:id", id=w.warehouse
        )


def test_transfer_receives_into_added_location_and_system_warehouse_is_protected(transferred):
    x = transferred
    service = WarehouseService(x.w.uow)
    wid = UUID(x.dest["id"])
    loc = service.add_location(x.f.maker, wid, body(service, x.f, wid), str(uuid4()))
    tid = x.create(request=x.body(destination_location_id=loc["id"]))
    x.review(tid)
    x.dispatch(tid)
    # Renaming a referenced warehouse/location does not invalidate a shipment in transit.
    service.rename(
        x.f.maker,
        wid,
        WarehouseRename(expected_version="1", name="到货区", reason="标识更正"),
        str(uuid4()),
        UUID(loc["id"]),
    )
    x.receive(tid, "10")
    assert (
        x.w.scalar("SELECT on_hand FROM med.stock_balance WHERE location_id=:id", id=loc["id"])
        == 10
    )
    assert x.stock(x.w.warehouse) == 0
    assert x.w.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=x.f.sku) == 10
    transit = UUID(x.detail(tid)["document"]["transit_warehouse_id"])
    assert all(
        r["id"] != str(transit) for r in service.listing(x.f.maker, x.w.entity, "", 0)["items"]
    )
    for action in (
        lambda: service.detail(x.f.maker, transit),
        lambda: service.add_location(x.f.maker, transit, body(service, x.f), str(uuid4())),
        lambda: service.rename(
            x.f.maker,
            transit,
            WarehouseRename(expected_version="1", name="人工改名", reason="测试"),
            str(uuid4()),
        ),
    ):
        with pytest.raises(BusinessError) as exc:
            action()
        assert exc.value.code == "SYSTEM_WAREHOUSE"


def test_location_permanent_retry_version_duplicate_and_rename_conflicts(flow):
    f = flow
    service = WarehouseService(f.world.uow)
    request = body(service, f)
    result = service.add_location(f.maker, f.world.warehouse, request, str(uuid4()))
    # A different HTTP key must still return the same durable business operation.
    assert service.add_location(f.maker, f.world.warehouse, request, str(uuid4())) == result
    with pytest.raises(BusinessError) as exc:
        service.add_location(
            f.maker,
            f.world.warehouse,
            request.model_copy(update={"name": "不同内容"}),
            str(uuid4()),
        )
    assert exc.value.code == "OPERATION_CONFLICT"
    for overrides, code in (
        ({}, "LOCATION_CODE_EXISTS"),
        ({"expected_version": "1"}, "VERSION_CONFLICT"),
    ):
        with pytest.raises(BusinessError) as exc:
            service.add_location(
                f.maker, f.world.warehouse, body(service, f, **overrides), str(uuid4())
            )
        assert exc.value.code == code
    rename = WarehouseRename(expected_version="1", name="修改后", reason="更正")
    key = str(uuid4())
    first = service.rename(f.maker, f.world.warehouse, rename, key, UUID(result["id"]))
    assert service.rename(f.maker, f.world.warehouse, rename, key, UUID(result["id"])) == first
    with pytest.raises(BusinessError) as exc:
        service.rename(f.maker, f.world.warehouse, rename, str(uuid4()), UUID(result["id"]))
    assert exc.value.code == "VERSION_CONFLICT"
    with pytest.raises(BusinessError) as exc:
        service.rename(
            f.maker,
            f.world.warehouse,
            rename.model_copy(update={"expected_version": "2"}),
            str(uuid4()),
            UUID(result["id"]),
        )
    assert exc.value.code == "WAREHOUSE_UNCHANGED"
    assert len(service.detail(f.maker, f.world.warehouse)["history"]) == 2


def test_location_add_race_has_one_winner_without_duplicate_audit(flow):
    f = flow
    service = WarehouseService(f.world.uow)
    requests = [body(service, f), body(service, f)]

    def run(request):
        try:
            return service.add_location(f.maker, f.world.warehouse, request, str(uuid4()))
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "VERSION_CONFLICT" in results
    assert len(service.detail(f.maker, f.world.warehouse)["history"]) == 1


@pytest.mark.parametrize("operation", ["create", "location", "rename", "rename-location"])
def test_warehouse_write_revocation_rolls_back_all_state(flow, operation):
    f = flow
    service = WarehouseService(f.world.uow)
    before = service.detail(f.maker, f.world.warehouse)
    request = body(service, f)
    key = str(uuid4())

    def run():
        if operation == "create":
            return service.create(
                f.maker,
                WarehouseCreate(
                    client_operation_id=request.client_operation_id,
                    entity_id=f.world.entity,
                    code="SECOND",
                    name="第二仓",
                    location_code="B1",
                ),
                key,
            )
        if operation == "location":
            return service.add_location(f.maker, f.world.warehouse, request, key)
        return service.rename(
            f.maker,
            f.world.warehouse,
            WarehouseRename(expected_version="1", name="新名称", reason="更正"),
            key,
            f.world.location if operation == "rename-location" else None,
        )

    def fail():
        raise BusinessError("REVOKED", "提交时撤权", 403)

    token = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError, match="提交时撤权"):
            run()
    finally:
        before_business_commit.reset(token)
    assert service.detail(f.maker, f.world.warehouse) == before
    assert f.world.scalar("SELECT count(*) FROM med.warehouse") == 1
    assert run()


def test_scope_is_checked_before_idempotency_and_child_requires_parent(flow):
    f = flow
    service = WarehouseService(f.world.uow)
    request = body(service, f)
    key = str(uuid4())
    service.add_location(f.maker, f.world.warehouse, request, key)
    for ctx in (
        replace(f.maker, tenant_id=uuid4()),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
    ):
        with pytest.raises(BusinessError):
            service.add_location(ctx, f.world.warehouse, request, key)
    for ctx in (replace(f.maker, tenant_id=uuid4()), replace(f.maker, entity_ids=frozenset())):
        with pytest.raises(BusinessError):
            service.detail(ctx, f.world.warehouse)
    assert service.detail(
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]), f.world.warehouse
    )
    second = service.create(
        f.maker,
        WarehouseCreate(
            client_operation_id=uuid4(),
            entity_id=f.world.entity,
            code="SECOND",
            name="第二仓",
            location_code="L1",
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError) as exc:
        service.rename(
            f.maker,
            UUID(second["id"]),
            WarehouseRename(expected_version="1", name="错误归属", reason="测试"),
            str(uuid4()),
            f.world.location,
        )
    assert exc.value.code == "NOT_FOUND"
    f.world.execute(
        "UPDATE med.legal_entity SET status='SUSPENDED' WHERE id=:id", id=f.world.entity
    )
    with pytest.raises(BusinessError) as exc:
        service.add_location(
            f.maker, f.world.warehouse, body(service, f, code="AFTER"), str(uuid4())
        )
    assert exc.value.code == "ENTITY_UNAVAILABLE"


def test_warehouse_and_location_search_and_pagination(flow):
    f = flow
    service = WarehouseService(f.world.uow)
    for i in range(51):
        service.add_location(
            f.maker,
            f.world.warehouse,
            body(service, f, code=f"ZONE-{i:02}", name=f"货架{i:02}"),
            str(uuid4()),
        )
    page = service.detail(f.maker, f.world.warehouse)
    assert len(page["locations"]) == 50 and page["has_more"]
    assert len(page["history"]) == 50 and page["history_has_more"]
    second = service.detail(f.maker, f.world.warehouse, offset=50, history_offset=50)
    assert len(second["locations"]) == 2 and not second["has_more"]
    assert len(second["history"]) == 1 and not second["history_has_more"]
    assert not ({r["id"] for r in page["locations"]} & {r["id"] for r in second["locations"]})
    assert len(service.detail(f.maker, f.world.warehouse, "货架50")["locations"]) == 1
    assert service.listing(f.maker, f.world.entity, "WH1", 0)["items"][0]["location_count"] == 52
    assert service.listing(f.maker, f.world.entity, "不存在", 0)["items"] == []
    assert service.listing(f.maker, f.world.entity, "", 50)["items"] == []


def test_warehouse_dto_forbids_identity_changes_and_empty_names():
    for changes in (
        {"code": "NEW"},
        {"entity_id": uuid4()},
        {"warehouse_id": uuid4()},
        {"kind": "TRANSIT"},
        {"name": " "},
        {"name": "x" * 201},
        {"expected_version": 1},
    ):
        with pytest.raises(ValidationError):
            WarehouseRename.model_validate(
                {"name": "库位", "expected_version": "1", "reason": "更正"} | changes
            )
