from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_api import api as api
from test_catalog_master_data import add, code
from test_catalog_revisions import body, document, review, revise, target
from test_procurement import flow as flow
from test_product_field_rules import create
from test_product_field_rules import request as rules_request
from test_product_field_rules import save as save_rules

from medinv.modules.catalog.api.dictionaries import DictionaryUpdate
from medinv.modules.catalog.api.operations import OperationsUpdate
from medinv.modules.catalog.application.codes import CatalogCodes
from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.catalog.application.field_rules import ProductFieldRules
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def operation(f, kind="product", values=None, ctx=None, version=None, key=None):
    return CatalogOperations(f.world.uow).update(
        ctx or f.maker,
        "products" if kind == "product" else "parties",
        target(f, kind),
        OperationsUpdate(
            expected_version=version or document(f, kind)["record"]["version"],
            values=values
            or {"recommendation_level": "重点推荐", "abc_class": "A", "abc_secondary_class": "B"},
            reason="核对经营分类",
        ),
        key or str(uuid4()),
    )


def dictionary_request(f, key, **changes):
    current = CatalogDictionaries(f.world.uow).detail(f.maker, key)
    return DictionaryUpdate(
        **{
            "expected_version": current["version"],
            "mode": current["mode"],
            "items": current["items"],
            "reason": "核对字典",
            **changes,
        }
    )


def dictionary_save(f, key, request=None, ctx=None, command_key=None):
    return CatalogDictionaries(f.world.uow).update(
        ctx or f.maker, key, request or dictionary_request(f, key), command_key or str(uuid4())
    )


@pytest.mark.parametrize("kind", ["product", "party"])
def test_operational_change_preserves_approval_and_purchasing_eligibility(flow, kind):
    f = flow
    before = document(f, kind)["record"]
    key = str(uuid4())
    result = operation(f, kind, version=before["version"], key=key)
    assert operation(f, kind, version=before["version"], key=key) == result
    after = document(f, kind)
    assert result["state"] == "APPROVED" and result["version"] == "3"
    assert after["record"]["abc_class"] == "A" and after["record"]["abc_secondary_class"] == "B"
    for field in ["reviewed_by", "reviewed_at", "revision_editors"]:
        assert after["record"][field] == before[field]
    assert after["history"][0]["action"] == "catalog.business_updated"
    assert after["history"][0]["detail"]["before"]["abc_class"] == ""
    f.post(f.inspect(f.receive("1")["id"], "1"))
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 1
    with pytest.raises(BusinessError, match="没有变化"):
        operation(f, kind)
    with pytest.raises(BusinessError, match="已变化"):
        operation(f, kind, version=before["version"])


@pytest.mark.parametrize("kind", ["product", "party"])
def test_legacy_revision_splits_business_only_and_mixed_changes(flow, kind):
    f = flow
    row = document(f, kind)["record"]
    result = revise(
        f, kind, request=body(f, kind, search_alias="alias", valid_until=row["valid_until"])
    )
    assert result["state"] == "APPROVED"
    result = revise(f, kind, request=body(f, kind, search_alias="alias2", name="资质名称变化"))
    assert result["state"] == "APPROVED" and result["has_changes"]
    before = document(f, kind)["record"]
    operation(f, kind)
    assert document(f, kind)["record"]["revision_editors"] == before["revision_editors"]
    with pytest.raises(BusinessError, match="建档人"):
        review(f, kind, ctx=f.maker)
    review(f, kind)
    review(f, kind, decision="SUSPEND")
    assert (
        operation(f, kind, values={"business_note": "停用期间维护经营备注"})["state"] == "SUSPENDED"
    )


def test_operations_whitelist_scope_permissions_version_and_rollback(flow):
    f = flow
    for data in [{"name": "绕过审核"}, {"recommendation_level": 123}, {"abc_class": "a" * 501}]:
        with pytest.raises(ValidationError):
            OperationsUpdate(expected_version="2", values=data, reason="测试")
    for values in [
        {"contact_name": "商品不适用"},
        {"abc_class": "D"},
        {"recommendation_level": "未知"},
    ]:
        with pytest.raises(BusinessError):
            operation(f, values=values)
    operation(
        f,
        "party",
        values={
            "short_name": "简称",
            "contact_name": "联系人",
            "contact_phone": "010-12345678",
            "delivery_address": "测试地址",
        },
    )
    for ctx in [
        replace(f.maker, tenant_id=uuid4()),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
    ]:
        with pytest.raises(BusinessError):
            operation(f, "party", ctx=ctx)
    with pytest.raises(BusinessError):
        operation(f, "party", values={"contact_phone": "invalid"})
    pending = body(f, "product", specification="规格更正")
    operation(f)
    with pytest.raises(BusinessError, match="已变化"):
        revise(f, "product", request=pending)
    before = document(f, "product")

    def abort():
        raise BusinessError("ABORT", "提交前权限失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            operation(f, values={"business_note": "应回滚"})
    finally:
        before_business_commit.reset(token)
    assert document(f, "product") == before


def test_operations_concurrent_writes_share_master_version(flow):
    f = flow

    def submit(value):
        try:
            return operation(f, values={"business_note": value}, version="2")
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, ["甲", "乙"]))
    assert sum(isinstance(r, dict) for r in result) == 1
    assert "VERSION_CONFLICT" in result


def test_new_qualification_requirements_do_not_block_unrelated_operational_update(flow):
    f = flow
    save_rules(
        f, rules_request(f, device_model={"required": True}, search_alias={"required": True})
    )
    operation(f, values={"abc_class": "A"})
    with pytest.raises(BusinessError, match="必填"):
        operation(f, values={"search_alias": "", "business_note": "同时清空必填项"})
    operation(f, values={"search_alias": "ALIAS"})
    with pytest.raises(BusinessError, match="必填"):
        revise(f, "product", request=body(f, "product", name="需要补齐资质"))


def test_dictionary_strict_suggest_history_review_and_product_creation(flow):
    f = flow
    svc = CatalogDictionaries(f.world.uow)
    dictionary_save(
        f,
        "trade_name",
        dictionary_request(
            f,
            "trade_name",
            mode="STRICT",
            items=[
                {"value": "品牌甲", "order": 1, "active": True},
                {"value": "品牌乙", "order": 2, "active": False},
            ],
        ),
    )
    for brand in ["未知品牌", "品牌乙"]:
        with pytest.raises(BusinessError, match="字典值"):
            create(f, trade_name=brand)
    create(f, trade_name="品牌甲")
    revise(f, "product", request=body(f, "product", trade_name="品牌甲"))
    review(f, "product")
    items = svc.detail(f.maker, "trade_name")["items"]
    for item in items:
        item["active"] = False
    dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", items=items))
    revise(f, "product", request=body(f, "product", specification="保留停用历史值"))
    review(f, "product")
    assert document(f, "product")["record"]["trade_name"] == "品牌甲"
    with pytest.raises(BusinessError):
        create(f, trade_name="品牌甲")
    dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", mode="SUGGEST"))
    create(f, trade_name="自由品牌")
    with pytest.raises(BusinessError):
        create(f, trade_name="品牌甲")
    policy = ProductFieldRules(f.world.uow).get(f.maker, "DEVICE")
    assert (
        next(r for r in policy["fields"] if r["key"] == "trade_name")["dictionary"]["mode"]
        == "SUGGEST"
    )
    units = svc.detail(f.maker, "base_unit")["items"]
    dictionary_save(
        f,
        "base_unit",
        dictionary_request(f, "base_unit", items=[{**r, "active": False} for r in units]),
    )
    with pytest.raises(BusinessError, match="基本单位"):
        create(f, trade_name="自由品牌")


def test_dictionary_disabled_operations_keep_history_but_block_new_choices(flow):
    f = flow
    operation(f)
    abc = CatalogDictionaries(f.world.uow).detail(f.maker, "abc_class")["items"]
    dictionary_save(
        f,
        "abc_class",
        dictionary_request(f, "abc_class", items=[{**r, "active": r["value"] != "A"} for r in abc]),
    )
    operation(f, values={"abc_class": "A", "business_note": "保留旧分类"})
    with pytest.raises(BusinessError):
        operation(f, "party", values={"abc_class": "A"})
    operation(f, values={"abc_class": "C"})
    with pytest.raises(BusinessError):
        operation(f, values={"abc_class": "A"})


def test_dictionary_tenant_permission_version_retry_and_history_retention(flow):
    f = flow
    svc = CatalogDictionaries(f.world.uow)
    data = dictionary_request(f, "recommendation_level")
    data.items[0].order = 100
    key = str(uuid4())
    first = dictionary_save(f, "recommendation_level", data, command_key=key)
    assert dictionary_save(f, "recommendation_level", data, command_key=key) == first
    assert len(svc.detail(f.maker, "recommendation_level")["history"]) == 1
    other = replace(f.maker, tenant_id=uuid4())
    assert svc.detail(other, "recommendation_level")["version"] == "0"
    assert svc.detail(other, "recommendation_level")["history"] == []
    with pytest.raises(BusinessError, match="已变化"):
        dictionary_save(f, "recommendation_level", data)
    with pytest.raises(BusinessError):
        dictionary_save(
            f,
            "recommendation_level",
            data,
            ctx=replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
            command_key=key,
        )
    with pytest.raises(BusinessError, match="不可删除"):
        dictionary_save(
            f, "recommendation_level", dictionary_request(f, "recommendation_level", items=[])
        )
    for items in [[{"value": "D", "active": True, "order": 1}], []]:
        with pytest.raises(BusinessError):
            dictionary_save(f, "abc_class", dictionary_request(f, "abc_class", items=items))
    with pytest.raises(BusinessError):
        dictionary_save(
            f,
            "trade_name",
            dictionary_request(f, "trade_name", items=[{"value": "x" * 101, "order": 1}]),
        )
    with pytest.raises(BusinessError):
        dictionary_save(
            f,
            "trade_name",
            dictionary_request(
                f, "trade_name", items=[{"value": "x", "order": 1}, {"value": "x", "order": 2}]
            ),
        )


def test_dictionary_concurrent_configuration_and_commit_rollback(flow):
    f = flow
    data = dictionary_request(f, "trade_name", items=[{"value": "甲", "order": 1}])

    def submit(_):
        try:
            return dictionary_save(f, "trade_name", data)
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, range(2)))
    assert sum(isinstance(r, dict) for r in result) == 1 and "VERSION_CONFLICT" in result

    def abort():
        raise BusinessError("ABORT", "提交前失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", mode="STRICT"))
    finally:
        before_business_commit.reset(token)
    detail = CatalogDictionaries(f.world.uow).detail(f.maker, "trade_name")
    assert detail["version"] == "1" and detail["mode"] == "SUGGEST" and len(detail["history"]) == 1


def test_scheme_cards_filter_before_pagination_and_keep_history_separate(flow):
    f = flow
    for n in range(51):
        add(f, code(scheme="MSFX", namespace="PLATFORM-A", external_code=f"{n:04d}"))
    add(f, code(scheme="MEITUAN", namespace="SHOP-1", external_code="00001"))
    service = CatalogCodes(f.world.uow)
    first = service.list(f.maker, "skus", f.sku, 0, ["MSFX"])
    assert len(first["items"]) == 50 and first["has_more"]
    assert all(e["detail"]["scheme"] == "MSFX" for e in first["history"])
    assert len(service.list(f.maker, "skus", f.sku, 50, ["MSFX"])["items"]) == 1
    other = service.list(f.maker, "skus", f.sku, 0, ["MEITUAN"])
    assert len(other["items"]) == len(other["history"]) == 1 and not other["has_more"]


def test_http_dictionary_operations_and_scheme_filter_contract(api, flow):
    client, control = api
    f = flow
    operations = f"/api/v1/catalog/products/{f.product['id']}/operations"
    assert client.get("/api/v1/catalog/dictionaries").status_code == 403
    with control.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=array_append(permissions, "
                "'catalog.read') WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    assert len(client.get("/api/v1/catalog/dictionaries").json()["data"]["items"]) == 19
    assert client.get(operations).json()["data"]["state"] == "APPROVED"
    assert client.get("/api/v1/catalog/dictionaries/invalid").status_code == 422
    assert (
        client.post(
            operations,
            json={"expected_version": "2", "values": {"abc_class": "A"}, "reason": "拒绝越权"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/catalog/dictionaries/trade_name",
            json=dictionary_request(f, "trade_name").model_dump(mode="json"),
        ).status_code
        == 403
    )
    add(f, code(scheme="MSFX", namespace="PLATFORM", external_code="00001"))
    path = f"/api/v1/catalog/skus/{f.sku}/codes"
    assert client.get(path, params={"schemes": "MEITUAN"}).json()["data"]["items"] == []
    assert (
        client.get(path, params={"schemes": "MSFX"}).json()["data"]["items"][0]["external_code"]
        == "00001"
    )
    assert client.get(path, params={"schemes": "invalid"}).status_code == 422
