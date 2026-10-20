import csv
import io
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_product_master import body
from test_product_master import master as master

from medinv.modules.catalog.api.master import MasterImport, MasterSave, SchemaPublish, SchemaSave
from medinv.modules.catalog.domain.master_schema import PROFESSIONAL_FIELDS, SUBTYPES, stable_id
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("profile", ["herb", "food", "health", "fsmp", "infant", "service"])
def test_professional_fields_persist_project_import_and_keep_history(master, profile):
    service, config, ctx, world = master
    fields = [f for f in PROFESSIONAL_FIELDS if profile in f["profiles"]]
    values = {f["key"]: f["default_label"] + "：已核对原始资料" for f in fields}
    saved = service.save(ctx, body(profile, **values), str(uuid4()))
    detail = service.detail(ctx, UUID(saved["id"]))
    assert all(detail["values"][k] == v for k, v in values.items())
    assert len(detail["skus"]) == (0 if profile == "service" else 1)
    changed_key = fields[0]["key"]
    service.save(
        ctx,
        MasterSave(
            product_id=UUID(saved["id"]),
            category_node_id=UUID(detail["category_node_id"]),
            expected_version=1,
            form_revision=0,
            values={changed_key: "复核后的原文"},
        ),
        str(uuid4()),
    )
    updated = service.detail(ctx, UUID(saved["id"]))
    assert updated["values"][changed_key] == "复核后的原文"
    assert updated["approval_state"] == "DRAFT"
    assert all(updated["values"][k] == v for k, v in values.items() if k != changed_key)
    assert (
        service.history(ctx, UUID(saved["id"]), 1)["snapshot"][changed_key] == values[changed_key]
    )
    listed = service.listing(ctx, stable_id("node:" + profile), saved["code"], 0)
    assert listed["items"][0]["values"][changed_key] == "复核后的原文"
    assert changed_key in {c["key"] for c in listed["columns"]}
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(["name", "base_unit", "regulatory_subtype", *values])
    writer.writerow(
        [
            "专业字段导入",
            "次" if profile == "service" else "个",
            SUBTYPES[profile][0][0],
            *values.values(),
        ]
    )
    request = MasterImport(
        category_node_id=UUID(stable_id("node:" + profile)),
        form_revision=0,
        content=content.getvalue(),
    )
    assert service.import_csv(ctx, request)["valid"]
    imported = service.import_csv(ctx, request, str(uuid4()))
    assert (
        service.detail(ctx, UUID(imported["items"][0]["id"]))["values"][changed_key]
        == values[changed_key]
    )
    with pytest.raises(BusinessError, match="不可见"):
        service.detail(replace(ctx, tenant_id=uuid4()), UUID(saved["id"]))


def test_profile_type_length_and_published_required_rules_are_enforced(master):
    service, config, ctx, world = master
    for value in ["x" * 501, {"injected": "object"}, "未知"]:
        with pytest.raises(BusinessError, match="文本类型或长度"):
            service.save(ctx, body("fsmp", fsmp_formula_features=value), str(uuid4()))
    with pytest.raises(BusinessError, match="不适用"):
        service.save(ctx, body("daily", fsmp_formula_features="特医资料"), str(uuid4()))
    payload = config.get(ctx)["payload"]
    payload["rules"]["fsmp"]["fsmp_guidance_notice"].update(
        {
            "label": "核对指导提示",
            "required_when": {"field": "regulatory_subtype", "equals": "FSMP_REGISTERED"},
            "max_length": 80,
        }
    )
    draft = config.save(
        ctx, SchemaSave(base_revision=0, payload=payload, reason="企业条件规则"), str(uuid4())
    )
    config.publish(
        ctx,
        UUID(draft["id"]),
        SchemaPublish(
            expected_version=1, preview_token=draft["preview"]["preview_token"], reason="发布"
        ),
        str(uuid4()),
    )
    request = body("fsmp")
    request.form_revision = 1
    saved = service.save(ctx, request, str(uuid4()))  # Incomplete drafts remain permitted.
    issues = service.validate(ctx, request)["issues"]
    issue = next(i for i in issues if i["field"] == "fsmp_guidance_notice")
    assert issue["tab"] == "quality" and "核对指导提示" in issue["message"]
    request.values["fsmp_guidance_notice"] = "按已核对注册标签填写"
    assert not any(
        i["field"] == "fsmp_guidance_notice" for i in service.validate(ctx, request)["issues"]
    )
    request.values["fsmp_guidance_notice"] = "x" * 81
    with pytest.raises(BusinessError, match="文本类型或长度"):
        service.save(ctx, request, str(uuid4()))
    assert service.detail(ctx, UUID(saved["id"]))["values"]["fsmp_guidance_notice"] is None


def test_migration_left_row_security_enabled_and_forced(master):
    _, _, _, world = master
    assert (
        world.scalar("""SELECT bool_and(relrowsecurity AND relforcerowsecurity)
        FROM pg_class WHERE oid IN ('med.product_master_detail'::regclass,
        'med.catalog_schema'::regclass,'med.audit_event'::regclass)""")
        is True
    )
