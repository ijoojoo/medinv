from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from test_product_master import body
from test_product_master import master as master

from medinv.modules.catalog.api.dictionaries import DictionaryUpdate
from medinv.modules.catalog.api.master import MasterImport, MasterSave, SchemaPublish, SchemaSave
from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.catalog.domain.master_schema import PROFILES, stable_id
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def dictionary(master, key="storage_conditions", **changes):
    _, _, ctx, world = master
    service = CatalogDictionaries(world.uow)
    current = service.detail(ctx, key)
    if changes:
        service.update(
            ctx,
            key,
            DictionaryUpdate(
                expected_version=current["version"],
                reason="主数据字典验收",
                **{**{k: current[k] for k in ("mode", "items")}, **changes},
            ),
            str(uuid4()),
        )
        return service.detail(ctx, key)
    return current


def items(*values):
    return [{"value": value, "active": True, "order": i} for i, value in enumerate(values)]


def draft(master, **rule):
    _, config, ctx, _ = master
    current = config.get(ctx)
    payload = current["payload"]
    payload["rules"]["food"]["storage_conditions"].update(rule)
    return config.save(
        ctx,
        SchemaSave(
            base_revision=current["revision"],
            payload=payload,
            reason="食品储存规则验收",
        ),
        str(uuid4()),
    )


def publish(master, saved):
    _, config, ctx, _ = master
    return config.publish(
        ctx,
        UUID(saved["id"]),
        SchemaPublish(
            expected_version=saved["version"],
            preview_token=saved["preview"]["preview_token"],
            reason="发布验收配置",
        ),
        str(uuid4()),
    )


def test_dictionary_mapping_covers_ten_profiles_and_preserves_field_identity(master):
    _, config, ctx, _ = master
    response = config.get(ctx)
    assert set(response["dictionaries"]) == {
        "base_unit",
        "dosage_form",
        "trade_name",
        "storage_conditions",
        "drug_business_scope",
        "device_business_scope",
    }
    unit = dictionary(master, "base_unit")
    bindings = [b for b in unit["bindings"] if b.get("source") == "MDM"]
    assert {b["profile"] for b in bindings} == set(PROFILES)
    assert next(b for b in bindings if b["profile"] == "service")["label"] == "计费单位"
    assert all(b["required"] and b["max_length"] == 30 for b in bindings)
    scopes = dictionary(master, "drug_business_scope")["bindings"]
    assert {b["profile"] for b in scopes if b.get("source") == "MDM"} == {"drug", "herb"}
    assert "brand" not in response["dictionaries"]
    readonly = config.get(replace(ctx, permissions=frozenset({"catalog.read"})))
    assert readonly["dictionaries"] == response["dictionaries"]


@pytest.mark.parametrize(
    "required",
    [
        {"required": True},
        {"required_when": {"field": "regulatory_subtype", "equals": "PREPACKAGED"}},
    ],
)
def test_preview_and_publish_block_unfillable_mdm_dictionary_rule(master, required):
    _, config, ctx, _ = master
    dictionary(master, mode="STRICT", items=items("阴凉处", "12", "12345", "未知"))
    saved = draft(master, **required, validator="DIGITS", min_length=3, max_length=4)
    assert any("普通食品" in e and "字典" in e for e in saved["preview"]["errors"])
    with pytest.raises(BusinessError) as error:
        publish(master, saved)
    assert error.value.code == "CONFIG_INVALID"
    assert config.get(ctx)["revision"] == 0
    # Saving an invalid proposal is allowed, but it cannot become the live rules.
    assert config.snapshot(ctx, UUID(saved["id"]))["state"] == "DRAFT"
    current = dictionary(master)
    dictionary(master, items=[*current["items"], *items("0123")])
    fresh = config.snapshot(ctx, UUID(saved["id"]))
    assert fresh["preview"]["errors"] == []
    publish(master, fresh)


def test_dictionary_updates_honor_published_food_rules_and_tenant_isolation(master):
    _, config, ctx, world = master
    dictionary(master, items=items("阴凉处", "0123"))
    publish(
        master,
        draft(
            master, label="保管条件", required=True, validator="DIGITS", min_length=3, max_length=4
        ),
    )
    before = dictionary(master)
    only_bad = [{**i, "active": i["value"] != "0123"} for i in before["items"]]
    dictionary(master, items=only_bad)  # Suggest mode still permits valid manual text.
    with pytest.raises(BusinessError) as error:
        dictionary(master, mode="STRICT")
    assert error.value.code == "DICTIONARY_RULE_CONFLICT"
    dictionary(master, items=before["items"], mode="STRICT")
    with pytest.raises(BusinessError) as error:
        dictionary(master, items=only_bad)
    assert "普通食品 · 保管条件" in error.value.message
    binding = next(b for b in dictionary(master)["bindings"] if b["category"] == "MDM:food")
    assert binding["eligible_count"] == 1 and binding["incompatible_values"] == ["阴凉处"]
    other = replace(ctx, tenant_id=uuid4())
    isolated = CatalogDictionaries(world.uow).detail(other, "storage_conditions")
    assert isolated["version"] == "0" and isolated["items"] == []
    assert not any(b["blocked"] for b in isolated["bindings"])
    assert config.get(other)["revision"] == 0


def test_dictionary_change_invalidates_preview_even_if_still_compatible(master):
    _, config, ctx, _ = master
    saved = draft(master, label="保管条件")
    dictionary(master, items=items("阴凉处"))
    with pytest.raises(BusinessError) as error:
        publish(master, saved)
    assert error.value.code == "PREVIEW_STALE"
    fresh = config.snapshot(ctx, UUID(saved["id"]))
    assert fresh["preview"]["dictionary_versions"]["storage_conditions"] == "1"
    assert fresh["preview"]["preview_token"] != saved["preview"]["preview_token"]
    assert publish(master, fresh)["revision"] == 1


def test_suggest_dictionary_does_not_allow_impossible_minimum_length(master):
    _, config, ctx, _ = master
    current = config.get(ctx)
    current["payload"]["rules"]["service"]["base_unit"]["min_length"] = 31
    with pytest.raises(BusinessError, match="超出字段容量"):
        config.save(
            ctx,
            SchemaSave(
                base_revision=0,
                payload=current["payload"],
                reason="验证计费单位真实容量",
            ),
            str(uuid4()),
        )


def test_manual_import_validation_and_retired_historical_value_agree(master):
    service, _, ctx, _ = master
    dictionary(master, mode="STRICT", items=items("阴凉处", "常温"))
    saved = service.save(ctx, body("food", storage_conditions="阴凉处"), str(uuid4()))
    current = dictionary(master)
    dictionary(master, items=[{**i, "active": i["value"] != "阴凉处"} for i in current["items"]])
    with pytest.raises(BusinessError, match="字典"):
        service.save(ctx, body("food", storage_conditions="阴凉处"), str(uuid4()))
    with pytest.raises(BusinessError, match="字典"):
        service.save(ctx, body("food", storage_conditions="未入字典"), str(uuid4()))
    changed = MasterSave(
        product_id=UUID(saved["id"]),
        category_node_id=UUID(stable_id("node:food")),
        form_revision=0,
        expected_version=1,
        values={"brand": "更新品牌"},
    )
    service.save(ctx, changed, str(uuid4()))
    assert not any(
        i["field"] == "storage_conditions" for i in service.validate(ctx, changed)["issues"]
    )
    detail = service.detail(ctx, UUID(saved["id"]))
    assert detail["values"]["storage_conditions"] == "阴凉处"
    imported = service.import_csv(
        ctx,
        MasterImport(
            category_node_id=UUID(stable_id("node:food")),
            form_revision=0,
            content="name,base_unit,regulatory_subtype,storage_conditions\n"
            "食品,个,PREPACKAGED,阴凉处",
        ),
    )
    assert imported["valid"] is False
    assert service.save(ctx, body("food", storage_conditions="常温"), str(uuid4()))["id"]


def test_concurrent_dictionary_disable_and_mdm_publish_cannot_create_conflict(master):
    dictionary(master, mode="STRICT", items=items("阴凉处", "0123"))
    saved = draft(master, required=True, validator="DIGITS")
    update = [{**i, "active": i["value"] != "0123"} for i in dictionary(master)["items"]]
    barrier = Barrier(2)

    def run(action):
        barrier.wait(timeout=10)
        try:
            action()
            return "SAVED"
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                run,
                [
                    lambda: publish(master, saved),
                    lambda: dictionary(master, items=update),
                ],
            )
        )
    assert results.count("SAVED") == 1
    assert set(results) <= {"SAVED", "CONFIG_INVALID", "DICTIONARY_RULE_CONFLICT"}
    assert not any(b["blocked"] for b in dictionary(master)["bindings"])
