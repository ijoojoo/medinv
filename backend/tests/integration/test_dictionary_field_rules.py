from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from test_catalog_cards_dictionaries import dictionary_request, dictionary_save
from test_procurement import flow as flow
from test_product_field_rules import create, request, save

from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.catalog.application.field_rules import ProductFieldRules
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def items(*values):
    return [{"value": v, "active": True, "order": i} for i, v in enumerate(values)]


@pytest.mark.parametrize(
    "required",
    [
        {"required": True},
        {"required_when": [{"field": "device_class", "equals": "III"}]},
    ],
)
def test_impossible_strict_required_rules_rejected_without_changes(flow, required):
    f = flow
    dictionary_save(
        f,
        "trade_name",
        dictionary_request(f, "trade_name", mode="STRICT", items=items("中文", "AB", "12345")),
    )
    rules = {**required, "validator": "DIGITS", "min_length": 3, "max_length": 4}
    with pytest.raises(BusinessError) as error:
        save(f, request(f, trade_name=rules))
    assert error.value.code == "DICTIONARY_RULE_CONFLICT"
    assert "医疗器械" in error.value.message
    policy = ProductFieldRules(f.world.uow).get(f.maker, "DEVICE")
    assert policy["version"] == "0" and policy["history"] == []
    current = dictionary_request(f, "trade_name")
    dictionary_save(
        f,
        "trade_name",
        dictionary_request(
            f, "trade_name", items=[*[i.model_dump() for i in current.items], *items("0123")]
        ),
    )
    save(f, request(f, trade_name=rules))
    create(f, device_class="III", trade_name="0123")
    detail = CatalogDictionaries(f.world.uow).detail(f.maker, "trade_name")
    device = next(b for b in detail["bindings"] if b["category"] == "DEVICE")
    assert device["eligible_count"] == 1 and not device["blocked"]
    assert set(device["incompatible_values"]) == {"中文", "AB", "12345"}


@pytest.mark.parametrize(
    "validator,bad,good",
    [
        ("NO_WHITESPACE", "A B", "A-B"),
        ("ASCII_CODE", "中文", "A_1/a.2-3"),
        ("DIGITS", "１２３", "00123"),
    ],
)
def test_dictionary_modes_optional_fields_and_formatted_selection(flow, validator, bad, good):
    f = flow
    dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", items=items(bad)))
    save(f, request(f, trade_name={"required": True, "validator": validator}))
    # Suggested options do not prevent valid free text; they are filtered in the browser.
    create(f, trade_name=good)
    with pytest.raises(BusinessError) as error:
        create(f, trade_name=bad)
    assert error.value.code == "PRODUCT_FIELD_INVALID"
    before = CatalogDictionaries(f.world.uow).detail(f.maker, "trade_name")
    with pytest.raises(BusinessError) as error:
        dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", mode="STRICT"))
    assert error.value.code == "DICTIONARY_RULE_CONFLICT"
    assert CatalogDictionaries(f.world.uow).detail(f.maker, "trade_name") == before
    save(f, request(f, trade_name={"required": False}))
    dictionary_save(f, "trade_name", dictionary_request(f, "trade_name", mode="STRICT"))
    create(f)


def test_dictionary_checks_both_product_categories_and_tenant_isolation(flow):
    f = flow
    dictionary_save(
        f,
        "trade_name",
        dictionary_request(f, "trade_name", mode="STRICT", items=items("123", "ABC")),
    )
    save(f, request(f, "DRUG", trade_name={"required": True, "validator": "DIGITS"}), "DRUG")
    change = dictionary_request(f, "trade_name")
    change.items[0].active = False
    with pytest.raises(BusinessError) as error:
        dictionary_save(f, "trade_name", change)
    assert error.value.code == "DICTIONARY_RULE_CONFLICT" and "药品" in error.value.message
    service = CatalogDictionaries(f.world.uow)
    other = service.detail(replace(f.maker, tenant_id=uuid4()), "trade_name")
    assert other["version"] == "0" and other["items"] == [] and other["history"] == []
    assert all(not b["required"] for b in other["bindings"])
    unchanged = service.detail(f.maker, "trade_name")
    assert unchanged["version"] == "1" and len(unchanged["history"]) == 1


def test_base_unit_requires_usable_strict_choice_but_optional_business_fields_can_be_empty(flow):
    f = flow
    change = dictionary_request(f, "base_unit", mode="STRICT")
    for item in change.items:
        item.active = False
    with pytest.raises(BusinessError) as error:
        dictionary_save(f, "base_unit", change)
    assert error.value.code == "DICTIONARY_RULE_CONFLICT"
    change.mode = "SUGGEST"
    dictionary_save(f, "base_unit", change)
    for key in ["abc_class", "recommendation_level"]:
        change = dictionary_request(f, key)
        for item in change.items:
            item.active = False
        dictionary_save(f, key, change)
        detail = CatalogDictionaries(f.world.uow).detail(f.maker, key)
        assert all(b["eligible_count"] == 0 and not b["blocked"] for b in detail["bindings"])


def test_parallel_policy_and_dictionary_changes_cannot_create_unfillable_required_field(flow):
    f = flow
    dictionary_save(
        f,
        "trade_name",
        dictionary_request(f, "trade_name", mode="STRICT", items=items("123", "ABC")),
    )
    policy = request(f, trade_name={"required": True, "validator": "DIGITS"})
    dictionary = dictionary_request(f, "trade_name")
    dictionary.items[0].active = False
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
                    lambda: save(f, policy),
                    lambda: dictionary_save(f, "trade_name", dictionary),
                ],
            )
        )
    assert sorted(results) == ["DICTIONARY_RULE_CONFLICT", "SAVED"]
    detail = CatalogDictionaries(f.world.uow).detail(f.maker, "trade_name")
    assert not any(b["blocked"] for b in detail["bindings"])
