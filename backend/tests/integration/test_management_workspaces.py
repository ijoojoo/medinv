from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.catalog.api.dictionaries import DictionaryUpdate
from medinv.modules.catalog.api.master import EntitySettingsSave
from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.catalog.application.entity_settings import ProductEntitySettings
from medinv.modules.inventory.application import sale_stops
from medinv.modules.workspace.management import Management, ManagementSave, MessageRead
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def dictionary(f, key, items=None):
    service = CatalogDictionaries(f.world.uow)
    current = service.detail(f.maker, key)
    if items is not None:
        service.update(
            f.maker,
            key,
            DictionaryUpdate(
                expected_version=current["version"],
                mode=current["mode"],
                items=items,
                reason="字典软件验收",
            ),
            str(uuid4()),
        )
        current = service.detail(f.maker, key)
    return current


def save(f, kind, payload, target=None, version="0", ctx=None, command_key=None):
    return Management(f.world.uow).save(
        ctx or f.maker,
        kind,
        target or uuid4(),
        ManagementSave(
            entity_id=f.world.entity, expected_version=version, payload=payload, reason="软件验收"
        ),
        command_key or str(uuid4()),
    )


def maintenance(f, **changes):
    return {
        "product_id": str(f.product["id"]),
        "lot_no": "B001",
        "checked_on": str(f.day),
        "next_due": str(f.day + timedelta(days=2)),
        "operator_name": "测试养护员",
        "checks": "外观、包装、存放条件检查",
        "result": "NORMAL",
        "state": "COMPLETED",
        **changes,
    }


def test_dictionary_identity_legacy_references_usage_and_protection(flow):
    f = flow
    before = dictionary(f, "base_unit")
    item = next(i for i in before["items"] if i["value"] == "个")
    assert before["usage"]["counts"]["个"] >= 1
    changed = [
        {**i, "description": "单位说明"} if i["id"] == item["id"] else i for i in before["items"]
    ]
    after = dictionary(f, "base_unit", changed)
    assert next(i for i in after["items"] if i["value"] == "个")["id"] == item["id"]
    assert f.world.scalar("SELECT base_unit FROM med.sku WHERE id=:id", id=f.sku) == "个"
    for field, value in [("id", str(uuid4())), ("code", "REPLACED")]:
        with pytest.raises(BusinessError, match="身份"):
            dictionary(
                f,
                "base_unit",
                [{**i, field: value} if i["id"] == item["id"] else i for i in after["items"]],
            )
    with pytest.raises(BusinessError, match="不可删除"):
        dictionary(f, "base_unit", [i for i in after["items"] if i["id"] != item["id"]])
    standard = dictionary(f, "staff_status")
    changed = dictionary(f, "staff_status", [{**i, "active": False} for i in standard["items"]])
    assert all(not i["active"] for i in changed["items"])


def test_hierarchy_cycles_parent_state_and_tenant_search(flow):
    f = flow
    parent, child = str(uuid4()), str(uuid4())
    items = [
        {"id": parent, "value": "质量部门测试", "code": "QA", "active": True, "order": 10},
        {
            "id": child,
            "parent_id": parent,
            "value": "养护组测试",
            "code": "CARE",
            "active": True,
            "order": 10,
        },
    ]
    saved = dictionary(f, "department", items)
    service = CatalogDictionaries(f.world.uow)
    assert service.search(f.maker, "CARE")["items"][0]["id"] == child
    assert service.search(replace(f.maker, tenant_id=uuid4()), "CARE")["items"] == []
    assert len(service.navigation(f.maker)["groups"]) == 7
    for changes in ({"parent_id": child}, {"parent_id": str(uuid4())}, {"active": False}):
        with pytest.raises(BusinessError):
            dictionary(
                f,
                "department",
                [{**i, **changes} if i["id"] == parent else i for i in saved["items"]],
            )
    result = save(f, "staff", {"code": "EMP001", "name": "测试职员", "department_id": child})
    assert (
        Management(f.world.uow).detail(f.maker, "staff", f.world.entity, UUID(result["id"]))[
            "payload"
        ]["department_id"]
        == child
    )


def test_dictionary_reordering_preserves_explicit_default_and_versions(flow):
    f = flow
    before = dictionary(f, "purchase_approval")
    items = before["items"] + [{"value": "审核同意测试", "active": True, "order": 0}]
    after = dictionary(f, "purchase_approval", items)
    assert next(i["value"] for i in after["items"] if i["is_default"]) == "同意"
    with pytest.raises(BusinessError, match="默认"):
        dictionary(
            f,
            "purchase_approval",
            [{**i, "active": False} if i["is_default"] else i for i in after["items"]],
        )
    with pytest.raises(BusinessError, match="已变化"):
        CatalogDictionaries(f.world.uow).update(
            f.maker,
            "purchase_approval",
            DictionaryUpdate(expected_version="0", mode="STRICT", items=items, reason="旧版本"),
            str(uuid4()),
        )


def test_staff_crud_auth_isolation_conflict_and_audit(flow):
    f = flow
    svc = Management(f.world.uow)
    target = uuid4()
    payload = {"code": "EMP001", "name": "测试职员"}
    key = str(uuid4())
    first = save(f, "staff", payload, target, command_key=key)
    assert save(f, "staff", payload, target, command_key=key) == first
    assert (
        svc.detail(f.maker, "staff", f.world.entity, target)["history"][0]["detail"]["after"][
            "name"
        ]
        == "测试职员"
    )
    for ctx in (
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, tenant_id=uuid4()),
    ):
        with pytest.raises(BusinessError):
            save(f, "staff", payload, ctx=ctx)
    with pytest.raises(BusinessError, match="已存在"):
        save(f, "staff", payload)
    with pytest.raises(BusinessError, match="不可修改"):
        save(f, "staff", {**payload, "code": "EMP002"}, target, "1")

    def change(name):
        try:
            save(f, "staff", {**payload, "name": name}, target, "1")
            return "ok"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(change, ["甲", "乙"]))
    assert sorted(result) == ["VERSION_CONFLICT", "ok"]
    with pytest.raises(BusinessError):
        save(f, "staff", {**payload, "permissions": ["OWNER"]})


def test_approval_design_is_draft_only_and_audited(flow):
    f = flow
    before = f.detail()["order"]
    payload = {
        "name": "采购二级审核设计",
        "business": "PURCHASE",
        "steps": [{"id": str(uuid4()), "name": "质量审核", "role": "QUALITY", "mode": "ALL"}],
    }
    result = save(f, "approval_design", payload)
    assert (
        Management(f.world.uow).detail(
            f.maker, "approval_design", f.world.entity, UUID(result["id"])
        )["payload"]["steps"][0]["mode"]
        == "ALL"
    )
    assert f.detail()["order"] == before
    with pytest.raises(BusinessError):
        save(f, "approval_design", {**payload, "steps": []})
    with pytest.raises(BusinessError):
        save(f, "approval_design", {**payload, "enabled": True})


def test_maintenance_immutable_no_stock_effect_and_readonly_inspection_records(flow):
    f = flow
    svc = Management(f.world.uow)
    before = f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance")
    result = save(f, "maintenance", maintenance(f))
    target = UUID(result["id"])
    assert f.world.scalar("SELECT sum(on_hand) FROM med.stock_balance") == before
    with pytest.raises(BusinessError, match="不能覆写"):
        save(f, "maintenance", maintenance(f, checks="改写已留档记录"), target, "1")
    detail = svc.detail(f.maker, "maintenance", f.world.entity, target)
    save(f, "maintenance", {**detail["payload"], "state": "VOID"}, target, "1")
    assert svc.detail(f.maker, "maintenance", f.world.entity, target)["payload"]["state"] == "VOID"
    f.inspect(f.receive("2")["id"], "1", "1")
    records = svc.quality_records(f.maker, f.world.entity, "", 0)["items"]
    assert len(records) == 1 and str(records[0]["qualified_qty"]) in {"1", "1.0"}
    assert records[0]["product_name"] == "采购测试商品" and records[0]["base_unit"] == "个"


def test_messages_read_is_personal_settings_effective_and_maintenance_reminders(flow):
    f = flow
    svc = Management(f.world.uow)
    first = svc.messages(f.maker, f.world.entity, "work", 0)["items"][0]
    key = str(uuid4())
    body = MessageRead(entity_id=f.world.entity, source="work", message_id=first["message_id"])
    assert svc.mark_read(f.maker, body, key) == svc.mark_read(f.maker, body, key)
    assert svc.messages(f.maker, f.world.entity, "work", 0)["items"][0]["read"]
    assert not svc.messages(f.reviewer, f.world.entity, "work", 0)["items"][0]["read"]
    save(f, "maintenance", maintenance(f))
    assert len(svc.messages(f.maker, f.world.entity, "maintenance", 0)["items"]) == 1
    config = save(f, "settings", {"reminders_enabled": False})
    assert svc.messages(f.maker, f.world.entity, "work", 0)["disabled"]
    save(
        f,
        "settings",
        {"reminders_enabled": True, "maintenance_notice_days": 0},
        UUID(config["id"]),
        "1",
    )
    assert svc.messages(f.maker, f.world.entity, "maintenance", 0)["items"] == []


def test_sale_stop_blocks_new_sales_allocation_post_and_public_reserve(sale):
    s = sale
    f = s.f
    product = UUID(f.product["id"])
    ready = s.ready("1")

    def stop(stopped, version):
        return sale_stops.save(
            f.world.uow,
            f.reviewer,
            product,
            sale_stops.SaleStopSave(
                entity_id=f.world.entity,
                expected_version=version,
                stopped=stopped,
                reason="质量停售验证",
            ),
            str(uuid4()),
        )

    stop(True, 0)
    for action in (s.order, lambda: s.allocate("1"), lambda: s.post(ready)):
        with pytest.raises(BusinessError, match="未开放"):
            action()
    with pytest.raises(BusinessError):
        f.world.service.reserve(f.maker, f.world.demand("1", f.sku), str(uuid4()))
    # Ordinary catalog maintenance cannot override a quality hold.
    ProductEntitySettings(f.world.uow).save(
        f.maker,
        product,
        EntitySettingsSave(
            entity_id=f.world.entity,
            expected_version=1,
            sales_enabled=True,
            reason="普通经营设置维护",
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError, match="未开放"):
        s.order()
    stop(False, 2)
    s.post(ready)
    with pytest.raises(BusinessError, match="已变化"):
        stop(True, 0)
    with pytest.raises(BusinessError):
        sale_stops.save(
            f.world.uow,
            replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
            product,
            sale_stops.SaleStopSave(
                entity_id=f.world.entity, expected_version=2, stopped=True, reason="测试"
            ),
            str(uuid4()),
        )


def test_management_transaction_guard_rollback(flow):
    f = flow

    def abort():
        raise BusinessError("ABORT", "提交前权限变化", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            save(f, "staff", {"code": "ROLLBACK", "name": "回滚验证"})
    finally:
        before_business_commit.reset(token)
    assert Management(f.world.uow).listing(f.maker, "staff", f.world.entity)["items"] == []
