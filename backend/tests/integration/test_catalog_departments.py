import csv
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_catalog_cards_dictionaries import dictionary_request, dictionary_save
from test_catalog_revisions import body, document, review, revise
from test_procurement import flow as flow

from medinv.modules.catalog.api.code_rules import ProductCodeRuleUpdate
from medinv.modules.catalog.api.imports import ProductImport
from medinv.modules.catalog.api.prices import ReferencePricesUpdate
from medinv.modules.catalog.api.schemas import ProductCreate
from medinv.modules.catalog.application.code_rules import ProductCodeRules
from medinv.modules.catalog.application.imports import COLUMNS, ProductImports
from medinv.modules.catalog.application.prices import ReferencePrices
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def product_body(f, **changes):
    row = document(f, "product")["record"]
    data = {k: v for k, v in row.items() if k in ProductCreate.model_fields}
    return ProductCreate(
        **{
            **data,
            "code": None,
            "sku_code": None,
            "base_unit": "个",
            "name": "新商品-" + uuid4().hex[:10],
            **changes,
        }
    )


def code_body(f, category="DEVICE", **changes):
    row = ProductCodeRules(f.world.uow).get(f.maker, category)
    return ProductCodeRuleUpdate(
        **{k: row[k] for k in ("enabled", "prefix", "date_format", "digits")},
        **{
            "expected_version": row["version"],
            "start_number": row["next_number"],
            "reason": "测试编码规则",
            **changes,
        },
    )


def save_code(f, category="DEVICE", **changes):
    # Accept configurable rule fields without duplicate keyword arguments.
    current = code_body(f, category).model_dump() | changes
    return ProductCodeRules(f.world.uow).update(
        f.maker, category, ProductCodeRuleUpdate(**current), str(uuid4())
    )


def csv_body(f, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(COLUMNS.values())
    for changes in rows:
        data = product_body(f, **changes).model_dump(mode="json")
        writer.writerow([data.get(k) if data.get(k) is not None else "" for k in COLUMNS])
    return ProductImport(content=out.getvalue())


def test_unified_codes_automatic_rules_collision_skip_and_idempotence(flow):
    f = flow
    data = product_body(f, code="ONE-0001")
    first = f.catalog.create_product(f.maker, data, str(uuid4()))
    assert first["code"] == first["sku_code"] == "ONE-0001"
    save_code(f, prefix="TEST-", date_format="YYYYMMDD", digits=4, start_number=17)
    rules = ProductCodeRules(f.world.uow)
    example = rules.get(f.maker, "DEVICE")["example"]
    f.catalog.create_product(
        f.maker, product_body(f, code="LEGACY", sku_code=example), str(uuid4())
    )
    data = product_body(f)
    key = str(uuid4())
    automatic = f.catalog.create_product(f.maker, data, key)
    assert automatic["code"].endswith("0018") and automatic["sku_code"] == automatic["code"]
    assert f.catalog.create_product(f.maker, data, key) == automatic
    assert rules.get(f.maker, "DEVICE")["next_number"] == 19
    assert rules.get(f.maker, "DRUG")["next_number"] == 1
    with pytest.raises(BusinessError) as error:
        save_code(f, start_number=18)
    assert error.value.code == "CODE_SEQUENCE_BACKWARD"
    save_code(f, enabled=False)
    with pytest.raises(BusinessError, match="未启用自动编码"):
        f.catalog.create_product(f.maker, product_body(f), str(uuid4()))
    f.catalog.create_product(f.maker, product_body(f, code="MANUAL-OK"), str(uuid4()))


def test_parallel_auto_codes_are_unique_and_failed_creation_does_not_consume_number(flow):
    f = flow
    data = [product_body(f) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda p: f.catalog.create_product(f.maker, p, str(uuid4())), data))
    assert len({r["code"] for r in results}) == 4
    rules = ProductCodeRules(f.world.uow)
    assert rules.get(f.maker, "DEVICE")["next_number"] == 5

    def abort():
        raise BusinessError("ABORT", "提交前授权失败", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError, match="授权失败"):
            f.catalog.create_product(f.maker, product_body(f), str(uuid4()))
    finally:
        before_business_commit.reset(token)
    assert rules.get(f.maker, "DEVICE")["next_number"] == 5


def test_code_rule_permissions_version_and_tenant_scope(flow):
    f = flow
    service = ProductCodeRules(f.world.uow)
    data = ProductCodeRuleUpdate(**(code_body(f).model_dump() | {"prefix": "ABC"}))
    for role in ["VIEWER", "QUALITY", "PURCHASER"]:
        with pytest.raises(BusinessError) as error:
            service.update(
                replace(f.maker, permissions=ROLE_PERMISSIONS[role]), "DEVICE", data, str(uuid4())
            )
        assert error.value.status == 403
    service.update(f.maker, "DEVICE", data, str(uuid4()))
    with pytest.raises(BusinessError, match="已变化"):
        service.update(f.maker, "DEVICE", data, str(uuid4()))
    other = replace(f.maker, tenant_id=uuid4())
    assert service.get(other, "DEVICE")["prefix"] == "QX"
    assert service.get(other, "DEVICE")["history"] == []


def test_quality_scope_uses_dictionary_and_changes_require_independent_review(flow):
    f = flow
    key = "device_business_scope"
    dictionary_save(
        f, key, dictionary_request(f, key, items=[{"value": "TEST-14", "active": True, "order": 1}])
    )
    with pytest.raises(BusinessError) as error:
        revise(f, "product", request=body(f, "product", device_business_scope="UNKNOWN"))
    assert error.value.code == "DICTIONARY_VALUE_INVALID"
    revise(
        f,
        "product",
        request=body(f, "product", device_business_scope="TEST-14", quality_note="按文件核对"),
    )
    assert document(f, "product")["record"]["approval_state"] == "DRAFT"
    with pytest.raises(BusinessError):
        review(f, "product", ctx=f.maker)
    review(f, "product")
    row = document(f, "product")["record"]
    assert row["approval_state"] == "APPROVED" and row["quality_note"] == "按文件核对"
    with pytest.raises(ValidationError):
        product_body(f, category="DRUG", device_business_scope="TEST-14")


def test_reference_prices_precise_isolated_versioned_and_do_not_reset_approval(flow):
    f = flow
    service = ReferencePrices(f.world.uow)
    before = document(f, "product")["record"]
    data = ReferencePricesUpdate(
        expected_version="0",
        purchase_price="0",
        wholesale_price="1.123456",
        retail_price="2.50",
        reason="核对参考价",
    )
    key = str(uuid4())
    result = service.update(f.maker, f.sku, data, key)
    assert service.update(f.maker, f.sku, data, key) == result
    current = service.get(f.maker, f.sku)
    assert current["values"]["purchase_price"] == "0.000000"
    assert current["values"]["wholesale_price"] == "1.123456"
    assert document(f, "product")["record"] == before
    assert document(f, "product")["history"][0]["action"] == "catalog.prices_updated"
    with pytest.raises(BusinessError, match="已变化"):
        service.update(f.maker, f.sku, data, str(uuid4()))
    with pytest.raises(BusinessError):
        service.get(replace(f.maker, tenant_id=uuid4()), f.sku)
    with pytest.raises(BusinessError):
        service.update(
            replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]), f.sku, data, str(uuid4())
        )
    for value in ["NaN", "-1", "1.0000001", "1e2", "1000000000000", " "]:
        with pytest.raises(ValidationError):
            ReferencePricesUpdate(expected_version="1", retail_price=value, reason="拒绝非法价格")


def test_import_preview_does_not_allocate_codes_and_atomic_submit_replays(flow):
    f = flow
    service = ProductImports(f.world.uow)
    data = csv_body(f, [{"name": "导入甲"}, {"name": "导入乙", "code": "000123"}])
    before = f.world.scalar("SELECT count(*) FROM med.product")
    assert service.preview(f.maker, data)["can_import"]
    assert ProductCodeRules(f.world.uow).get(f.maker, "DEVICE")["next_number"] == 1
    assert f.world.scalar("SELECT count(*) FROM med.product") == before
    key = str(uuid4())
    result = service.submit(f.maker, data, key)
    assert result["count"] == 2 and result["state"] == "DRAFT"
    assert result["items"][0]["code"] == "QX000001"
    assert result["items"][1]["code"] == "000123"
    assert service.submit(f.maker, data, key) == result
    assert f.world.scalar("SELECT count(*) FROM med.product") == before + 2
    assert not service.preview(f.maker, data)["can_import"]


def test_import_revalidates_after_preview_and_rejects_duplicates_without_partial_writes(flow):
    f = flow
    service = ProductImports(f.world.uow)
    data = csv_body(f, [{"name": "重复", "code": "SAME"}, {"name": "重复", "code": "SAME"}])
    preview = service.preview(f.maker, data)
    assert all(len(r["errors"]) >= 2 for r in preview["rows"])
    before = f.world.scalar("SELECT count(*) FROM med.product")
    with pytest.raises(BusinessError, match="第 2 行"):
        service.submit(f.maker, data, str(uuid4()))
    assert f.world.scalar("SELECT count(*) FROM med.product") == before
    valid = csv_body(f, [{"name": "自动入档"}, {"name": "手工入档", "code": "1000123"}])
    assert service.preview(f.maker, valid)["can_import"]
    save_code(f, enabled=False)
    with pytest.raises(BusinessError, match="未启用自动编码"):
        service.submit(f.maker, valid, str(uuid4()))
    assert f.world.scalar("SELECT count(*) FROM med.product") == before


def test_import_rolls_back_all_rows_and_numbering_on_commit_failure(flow):
    f = flow
    service = ProductImports(f.world.uow)
    data = csv_body(f, [{"name": "事务甲"}, {"name": "事务乙"}])
    before = f.world.scalar("SELECT count(*) FROM med.product")

    def abort():
        raise BusinessError("ABORT", "导入提交前授权失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            service.submit(f.maker, data, str(uuid4()))
    finally:
        before_business_commit.reset(token)
    assert f.world.scalar("SELECT count(*) FROM med.product") == before
    assert ProductCodeRules(f.world.uow).get(f.maker, "DEVICE")["next_number"] == 1
    with pytest.raises(BusinessError):
        service.preview(replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]), data)
    malformed = ProductImport(content=data.content.replace("名称", "不存在列", 1))
    with pytest.raises(BusinessError):
        service.preview(f.maker, malformed)
    assert not service.preview(
        f.maker, ProductImport(content=data.content.replace("DEVICE", "无效类别"))
    )["can_import"]
