from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_catalog_revisions import body, document, review, revise
from test_procurement import flow as flow

from medinv.modules.catalog.api.field_rules import FieldRule, FieldRulesUpdate, RequiredWhen
from medinv.modules.catalog.api.schemas import ProductCreate
from medinv.modules.catalog.application.field_rules import ProductFieldRules, validate_product
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def request(f, category="DEVICE", **changes):
    result = ProductFieldRules(f.world.uow).get(f.maker, category)
    fields = [
        FieldRule(**{k: value[k] for k in FieldRule.model_fields}) for value in result["fields"]
    ]
    fields = [FieldRule(**{**r.model_dump(), **changes.get(r.key, {})}) for r in fields]
    return FieldRulesUpdate(
        expected_version=result["version"], fields=fields, reason="测试字段管理要求"
    )


def save(f, data=None, category="DEVICE", ctx=None, key=None):
    return ProductFieldRules(f.world.uow).update(
        ctx or f.maker, category, data or request(f), key or str(uuid4())
    )


def create(f, **changes):
    row = document(f, "product")["record"]
    values = {k: row[k] for k in ProductCreate.model_fields if k in row}
    suffix = str(uuid4())[:8]
    values.update(code=suffix, sku_code=suffix, base_unit="个", **changes)
    return f.catalog.create_product(f.maker, ProductCreate(**values), str(uuid4()))


def test_category_settings_are_isolated_versioned_audited_and_idempotent(flow):
    f = flow
    service = ProductFieldRules(f.world.uow)
    default = service.get(f.maker, "DEVICE")
    assert default["version"] == "0"
    data = request(f, device_model={"required": True, "order": 1, "max_length": 40})
    key = str(uuid4())
    first = save(f, data, key=key)
    assert save(f, data, key=key) == first
    result = service.get(f.maker, "DEVICE")
    assert result["version"] == "1" and result["fields"][0]["key"] == "device_model"
    assert len(result["history"]) == 1
    assert result["history"][0]["detail"]["before"] != result["history"][0]["detail"]["after"]
    assert service.get(f.maker, "DRUG")["version"] == "0"
    other = replace(f.maker, tenant_id=uuid4())
    assert service.get(other, "DEVICE")["version"] == "0"
    assert service.get(other, "DEVICE")["history"] == []
    assert document(f, "product")["record"]["approval_state"] == "APPROVED"
    with pytest.raises(BusinessError, match="已被修改"):
        save(f, data)


def test_required_rules_apply_to_creation_revision_and_review_without_partial_writes(flow):
    f = flow
    save(f, request(f, device_model={"required": True}, device_class={"required": True}))
    for action in [lambda: create(f), lambda: revise(f, "product"), lambda: review(f, "product")]:
        with pytest.raises(BusinessError) as exc:
            action()
        assert exc.value.code == "PRODUCT_FIELD_INVALID"
        assert "器械型号必填" in exc.value.message and "器械管理类别必填" in exc.value.message
    assert document(f, "product")["record"]["version"] == "2"
    revise(f, "product", request=body(f, "product", device_class="I", device_model="M1"))
    review(f, "product")
    assert document(f, "product")["record"]["approval_state"] == "APPROVED"
    create(f)


@pytest.mark.parametrize(
    "device_class,expected", [("I", "产品备案号"), ("II", "注册证号"), ("III", "注册证号")]
)
def test_statutory_certificate_linkage_and_server_validation(flow, device_class, expected):
    f = flow
    # Validate a persisted/merged record as used by review, independently of Pydantic.
    values = {
        **document(f, "product")["record"],
        "device_class": device_class,
        "approval_number": "",
    }
    with f.world.uow.open(f.maker) as session:
        with pytest.raises(BusinessError, match=expected + "必填"):
            validate_product(SqlSession(session), f.maker, values)
    save(
        f,
        request(f, registrant_name={"required_when": [{"field": "device_class", "equals": "III"}]}),
    )
    if device_class == "III":
        with pytest.raises(BusinessError, match="注册人必填"):
            create(f, device_class=device_class)
    else:
        create(f, device_class=device_class)
    create(f, device_class=device_class, registrant_name="测试主体")


@pytest.mark.parametrize(
    "validator,good,bad",
    [
        ("NO_WHITESPACE", "含中文编号", "A B"),
        ("ASCII_CODE", "M-1/A_2.x", "型号甲"),
        ("DIGITS", "00123", "１２３"),
    ],
)
def test_format_length_and_hidden_value_checks(flow, validator, good, bad):
    f = flow
    save(
        f,
        request(
            f,
            device_model={
                "min_length": 3,
                "max_length": 12,
                "validator": validator,
                "visible": False,
            },
        ),
    )
    create(f, device_model=good)
    for value in [bad, "1", "1" * 13]:
        with pytest.raises(BusinessError) as exc:
            create(f, device_model=value)
        assert exc.value.code == "PRODUCT_FIELD_INVALID"
    # Optional and hidden is still optional; do not destroy previously stored data.
    create(f)


def test_configuration_rejects_weakened_core_rules_bad_fields_and_cyclic_conditions(flow):
    f = flow
    for changes in [
        {"name": {"required": False}},
        {"device_class": {"visible": False}},
        {"device_model": {"max_length": 201}},
        {"device_model": {"required": True, "visible": False}},
        {"device_class": {"required_when": [{"field": "device_class", "equals": "I"}]}},
        {"device_model": {"required_when": [{"field": "drug_sale_class", "equals": "RX"}]}},
        {"valid_from": {"validator": "DIGITS"}},
    ]:
        with pytest.raises(BusinessError) as exc:
            save(f, request(f, **changes))
        assert exc.value.code == "FIELD_RULE_INVALID"
    data = request(f)
    data.fields[0] = data.fields[1]
    with pytest.raises(BusinessError):
        save(f, data)
    with pytest.raises(ValidationError):
        RequiredWhen(field="approval_number", equals="I")
    assert ProductFieldRules(f.world.uow).get(f.maker, "DEVICE")["version"] == "0"


def test_only_administrators_configure_and_parallel_writes_conflict(flow):
    f = flow
    data = request(f, trade_name={"required": True})
    service = ProductFieldRules(f.world.uow)
    for role in ["OPERATOR", "QUALITY", "PURCHASER", "VIEWER"]:
        ctx = replace(f.maker, permissions=ROLE_PERMISSIONS[role])
        assert service.get(ctx, "DEVICE")["version"] == "0"
        with pytest.raises(BusinessError) as exc:
            save(f, data, ctx=ctx)
        assert exc.value.status == 403

    def update():
        try:
            return save(f, data)["version"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: update(), range(2)))
    assert sorted(results) == ["1", "VERSION_CONFLICT"]


def test_old_client_revision_cannot_clear_or_bypass_new_rules(flow):
    f = flow
    revise(f, "product", request=body(f, "product", device_model="VALID-MODEL"))
    save(f, request(f, device_model={"required": True}))
    from medinv.modules.catalog.api.schemas import PROFILE_FIELDS, ProductRevision

    values = {
        k: v
        for k, v in body(f, "product", name="旧客户端修改").model_dump().items()
        if k not in PROFILE_FIELDS
    }
    revise(f, "product", request=ProductRevision(**values))
    assert document(f, "product")["record"]["device_model"] == "VALID-MODEL"
    with pytest.raises(BusinessError):
        revise(f, "product", request=body(f, "product", device_model=""))


def test_device_filing_and_registration_switch_requires_corresponding_number(flow):
    f = flow
    revise(
        f, "product", request=body(f, "product", device_class="I", approval_number="TEST-FILING")
    )
    with pytest.raises(BusinessError) as exc:
        revise(f, "product", request=body(f, "product", device_class="II"))
    assert exc.value.code == "PRODUCT_CREDENTIAL_CHANGED"
    revise(
        f,
        "product",
        request=body(f, "product", device_class="II", approval_number="TEST-REGISTRATION"),
    )
    assert document(f, "product")["record"]["approval_number"] == "TEST-REGISTRATION"


def test_drug_conditional_fields_do_not_affect_devices(flow):
    f = flow
    save(
        f,
        request(
            f,
            "DRUG",
            authorization_holder={"required_when": [{"field": "drug_sale_class", "equals": "RX"}]},
        ),
        category="DRUG",
    )
    create(f)
    create(f, category="DRUG", drug_sale_class="OTC")
    with pytest.raises(BusinessError, match="上市许可持有人必填"):
        create(f, category="DRUG", drug_sale_class="RX")
    create(f, category="DRUG", drug_sale_class="RX", authorization_holder="测试持有人")
