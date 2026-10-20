import json
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_catalog_revisions import document
from test_procurement import flow as flow
from test_product_field_rules import create, request, save

from medinv.modules.catalog.api.field_rules import FieldRule, FieldRulesUpdate
from medinv.modules.catalog.api.operations import OperationsUpdate
from medinv.modules.catalog.application.field_policy import field_label
from medinv.modules.catalog.application.field_rules import ProductFieldRules
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_templates_keep_semantics_and_governance_separate_from_display(flow):
    f = flow
    service = ProductFieldRules(f.world.uow)
    device = service.get(f.maker, "DEVICE")
    drug = service.get(f.maker, "DRUG")
    assert device["template"]["code"] == "DEVICE_BASE"
    assert drug["template"]["code"] == "DRUG_BASE"
    assert drug["template"]["variants"] == {}
    assert device["template"]["resolved_variant"]["code"] == "DEVICE_UNCLASSIFIED"
    fields = {r["key"]: r for r in device["fields"]}
    assert fields["device_model"]["card"] == "basic"
    assert fields["device_model"]["review_policy"] == "QUALITY_REVIEW"
    assert fields["search_alias"]["card"] == "operations"
    assert fields["search_alias"]["review_policy"] == "DIRECT_AUDIT"
    assert fields["approval_number"]["card"] == "quality"
    assert len({r["field_code"] for r in device["fields"]}) == len(device["fields"])
    assert (
        fields["name"]["field_code"]
        == next(r for r in drug["fields"] if r["key"] == "name")["field_code"]
    )
    for classification, label in [
        ("I", "备案人"),
        ("II", "注册人"),
        ("III", "注册人"),
        ("UNKNOWN", "注册人 / 备案人"),
    ]:
        rule = next(r for r in request(f).fields if r.key == "registrant_name")
        assert (
            field_label(fields["registrant_name"], rule, {"device_class": classification}) == label
        )


def test_alias_used_in_details_operations_validation_and_tenant_isolation(flow):
    f = flow
    save(
        f,
        request(
            f,
            device_model={"display_name": "业务型号", "required": True},
            search_alias={"display_name": "快捷检索词", "max_length": 8},
        ),
    )
    with pytest.raises(BusinessError, match="业务型号必填"):
        create(f)
    policy = document(f, "product")["field_policy"]
    model = next(r for r in policy["fields"] if r["key"] == "device_model")
    assert model["label"] == "器械型号" and model["display_name"] == "业务型号"
    other = replace(f.maker, tenant_id=uuid4())
    assert all(
        not r["display_name"] for r in ProductFieldRules(f.world.uow).get(other, "DEVICE")["fields"]
    )
    target = document(f, "product")["record"]["id"]
    service = CatalogOperations(f.world.uow)
    data = service.get(f.maker, "products", target)
    assert data["field_policy"]["version"] == policy["version"]
    assert data["field_policy"]["context"] == {"device_class": "UNKNOWN"}
    with pytest.raises(BusinessError, match="快捷检索词长度"):
        service.update(
            f.maker,
            "products",
            target,
            OperationsUpdate(
                expected_version=data["version"],
                values={"search_alias": "toolongvalue"},
                reason="测试名称",
            ),
            str(uuid4()),
        )
    changed = service.update(
        f.maker,
        "products",
        target,
        OperationsUpdate(
            expected_version=data["version"],
            values={"search_alias": "LOCAL"},
            reason="测试经营维护",
        ),
        str(uuid4()),
    )
    assert changed["state"] == "APPROVED"
    assert document(f, "product")["record"]["search_alias"] == "LOCAL"
    # Strengthening a quality field must not prevent an unrelated operational edit.
    assert document(f, "product")["record"]["device_model"] == ""
    created = create(f, device_model="M-1", device_class="I")
    from medinv.modules.catalog.application.maintenance import CatalogMaintenance

    detail = CatalogMaintenance(f.world.uow).detail(f.maker, "product", created["id"], 0)
    assert detail["field_policy"]["template"]["resolved_variant"]["code"] == "DEVICE_I"


@pytest.mark.parametrize(
    "changes",
    [
        {"device_model": {"display_name": "生产企业"}},
        {"device_model": {"display_name": "商品 编码"}},
        {"device_model": {"display_name": "注册人"}},
        {"device_model": {"display_name": "ＭＯＤＥＬ"}, "trade_name": {"display_name": "model"}},
        {"registrant_name": {"display_name": "企业"}},
        {"approval_number": {"display_name": "编号"}},
    ],
)
def test_ambiguous_or_system_owned_alias_rejected_without_partial_save(flow, changes):
    with pytest.raises(BusinessError) as exc:
        save(flow, request(flow, **changes))
    assert exc.value.code == "FIELD_RULE_INVALID"
    assert ProductFieldRules(flow.world.uow).get(flow.maker, "DEVICE")["version"] == "0"


def test_old_policy_and_client_preserve_alias_and_idempotent_replays(flow):
    f = flow
    data = request(f, device_model={"order": 2})
    legacy = data.model_dump()
    for item in legacy["fields"]:
        item.pop("display_name")
    data = FieldRulesUpdate(**legacy)
    key = str(uuid4())
    result = save(f, data, key=key)
    # Simulate stored policy written by the previous release.
    with f.world.uow.open(f.maker) as session:
        SqlSession(session).execute(
            "UPDATE med.product_field_rules SET fields=CAST(:v AS jsonb) WHERE category='DEVICE'",
            {"v": json.dumps(legacy["fields"])},
        )
    current = ProductFieldRules(f.world.uow).get(f.maker, "DEVICE")
    assert all(r["display_name"] == "" for r in current["fields"])
    save(f, request(f, device_model={"display_name": "业务型号"}))
    assert save(f, data, key=key) == result
    legacy = request(f, device_model={"order": 3}).model_dump()
    for item in legacy["fields"]:
        item.pop("display_name")
    save(f, FieldRulesUpdate(**legacy))
    current = ProductFieldRules(f.world.uow).get(f.maker, "DEVICE")
    assert (
        next(r for r in current["fields"] if r["key"] == "device_model")["display_name"]
        == "业务型号"
    )
    save(f, request(f, device_model={"display_name": ""}))
    assert next(r for r in request(f).fields if r.key == "device_model").display_name == ""


def test_template_governance_cannot_be_supplied_as_tenant_rules(flow):
    raw = request(flow).fields[0].model_dump()
    for key, value in [
        ("review_policy", "DIRECT_AUDIT"),
        ("card", "operations"),
        ("field_code", "another"),
    ]:
        with pytest.raises(ValidationError):
            FieldRule(**{**raw, key: value})
    for label in ["x" * 41, "a\tb"]:
        with pytest.raises(ValidationError):
            FieldRule(**{**raw, "display_name": label})
