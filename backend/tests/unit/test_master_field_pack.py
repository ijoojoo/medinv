import json
import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from medinv.modules.catalog.application.master_config import (
    business_rules_changed,
    configuration_errors,
)
from medinv.modules.catalog.domain.master_schema import PROFESSIONAL_FIELDS, default_schema, field

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = runpy.run_path(
    str(ROOT / "backend/migrations/business/versions/0041_master_professional_fields.py")
)


def original():
    return json.loads(
        (ROOT / "contracts/mdm/product-master-form.v1.json").read_text(encoding="utf-8")
    )


def test_field_pack_is_frozen_and_preserves_all_original_slots_and_rules():
    assert MIGRATION["FIELD_PACK"] == PROFESSIONAL_FIELDS
    before = original()
    before["rules"]["food"]["brand"]["label"] = "企业品牌"
    before["rules"]["food"]["brand"]["required"] = True
    snapshot = deepcopy(before)
    after = MIGRATION["extend_schema"](before)
    assert before == snapshot
    assert len(after["fields"]) == 76
    assert after["fields"][:58] == before["fields"]
    assert after["slots"][:58] == before["slots"]
    assert after["nodes"] == before["nodes"]
    for profile, rules in before["rules"].items():
        assert all(after["rules"][profile][key] == rule for key, rule in rules.items())
        assert all(not after["rules"][profile][f["key"]]["required"] for f in PROFESSIONAL_FIELDS)
    assert configuration_errors(after, before) == []
    assert MIGRATION["extend_schema"](after) == after
    assert default_schema()["slots"][:15] == before["slots"][:15]


def test_upgrade_keeps_custom_fields_and_handles_slot_name_collisions():
    before = original()
    custom = field("custom_external_note", "自定义备注", "custom", binding="custom")
    custom["lifecycle"] = "CUSTOM"
    before["fields"].append(custom)
    before["columns"].append(
        {
            "key": custom["key"],
            "label": custom["default_label"],
            "value_type": "text",
            "projection": custom["key"],
        }
    )
    before["slots"].append(
        {
            "slot_id": "E_022",
            "field_key": custom["key"],
            "tab": "custom",
            "position": 0,
            "height": 94,
            "span": 1,
        }
    )
    for rules in before["rules"].values():
        rules[custom["key"]] = {
            "applicable": True,
            "label": "",
            "required": False,
            "read_only": False,
            "required_when": None,
        }
    after = MIGRATION["extend_schema"](before)
    assert after["slots"][:59] == before["slots"]
    assert configuration_errors(after, before) == []
    assert len({s["slot_id"] for s in after["slots"]}) == 77


def test_upgrade_never_replaces_an_existing_field_with_different_semantics():
    before = original()
    before["fields"].append({**PROFESSIONAL_FIELDS[0], "default_label": "冲突语义"})
    with pytest.raises(RuntimeError, match="identity collision"):
        MIGRATION["extend_schema"](before)


def test_optional_field_additions_do_not_force_business_edits_back_to_quality_review():
    before = original()
    after = MIGRATION["extend_schema"](before)
    repo = SimpleNamespace(rows=lambda *_: [{"payload": before}])
    current = {"revision": 1, "payload": after}
    assert not business_rules_changed(repo, 0, "fsmp", current)
    after["rules"]["fsmp"]["fsmp_guidance_notice"]["required"] = True
    assert business_rules_changed(repo, 0, "fsmp", current)
    assert not business_rules_changed(repo, 0, "daily", current)
