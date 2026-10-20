from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_catalog_master_data import add, code, state
from test_packaging import approved, create, review
from test_procurement import flow as flow

from medinv.modules.catalog.application.entry import EntryProducts
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("channel", ["purchase", "sale"])
def test_barcode_exact_identity_base_and_packaging_and_existing_sku_hydration(flow, channel):
    f = flow
    svc = EntryProducts(f.world.uow)
    add(f)
    pack = approved(f, "100")
    add(f, code(external_code="00000-BOX", packaging_id=UUID(pack["id"])))
    base = svc.search(f.maker, f.world.entity, channel, "00123-AbC")["items"]
    assert len(base) == 1 and base[0]["id"] == str(f.sku) and base[0]["eligible"]
    assert base[0]["barcode_match"] == {
        "barcode": "00123-AbC",
        "packaging_id": None,
        "unit_name": "个",
        "numerator": "1",
        "denominator": "1",
        "available": True,
    }
    boxed = svc.search(f.maker, f.world.entity, channel, "  00000-BOX  ")["items"]
    assert len(boxed) == 1 and boxed[0]["eligible"]
    assert boxed[0]["barcode_match"] == {
        "barcode": "00000-BOX",
        "packaging_id": pack["id"],
        "unit_name": "箱",
        "numerator": "100",
        "denominator": "1",
        "available": True,
    }
    for value in ["00123-abc", "123-AbC", "00123", "00000", "00000-BOX 个"]:
        assert svc.search(f.maker, f.world.entity, channel, value)["items"] == []
    assert svc.search(f.maker, f.world.entity, channel, "00000-BOX", 20)["items"] == []
    hydrated = svc.search(f.maker, f.world.entity, channel, "", sku_id=f.sku)["items"]
    assert len(hydrated) == 1 and hydrated[0]["barcode_match"] is None


def test_barcode_scope_state_and_scheme_are_enforced(flow):
    f = flow
    svc = EntryProducts(f.world.uow)
    created = add(f)
    other = replace(f.maker, tenant_id=uuid4())
    assert svc.search(other, f.world.entity, "purchase", "00123-AbC")["items"] == []
    for ctx in [
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, permissions=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            svc.search(ctx, f.world.entity, "purchase", "00123-AbC")
    state(f, created)
    assert svc.search(f.maker, f.world.entity, "purchase", "00123-AbC")["items"] == []
    # A traceability / insurer / platform identifier is not a commodity barcode.
    add(f, code(scheme="UDI_DI", external_code="UDI-ONLY"))
    add(f, code(scheme="CUSTOM", namespace="OTHER-ERP", external_code="ERP-ONLY"))
    for value in ["UDI-ONLY", "ERP-ONLY"]:
        assert svc.search(f.maker, f.world.entity, "purchase", value)["items"] == []
    long_code = "0" + "A" * 127
    add(f, code(external_code=long_code))
    assert svc.search(f.maker, f.world.entity, "purchase", long_code)["items"][0]["id"] == str(
        f.sku
    )


def test_retired_pack_barcode_is_disabled_and_does_not_fall_back_to_base_or_new_pack(flow):
    f = flow
    svc = EntryProducts(f.world.uow)
    pack = approved(f, "100")
    add(f, code(external_code="OLD-BOX", packaging_id=UUID(pack["id"])))
    review(f, create(f, "200"))  # Replacing the same named unit retires the old version.
    row = svc.search(f.maker, f.world.entity, "purchase", "OLD-BOX")["items"][0]
    assert not row["eligible"] and not row["barcode_match"]["available"]
    assert row["barcode_match"]["packaging_id"] == pack["id"]
    assert row["barcode_match"]["numerator"] == "100"
    assert svc.search(f.maker, f.world.entity, "purchase", "S-NEW")["items"][0]["eligible"]


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE med.product SET approval_state='DRAFT'",
        "UPDATE med.stock_gate SET blocked=true",
        "UPDATE med.legal_entity SET status='SUSPENDED'",
    ],
)
def test_barcode_does_not_bypass_business_eligibility(flow, change):
    f = flow
    add(f)
    f.world.execute(change)
    for channel in ["purchase", "sale"]:
        row = EntryProducts(f.world.uow).search(f.maker, f.world.entity, channel, "00123-AbC")[
            "items"
        ][0]
        assert not row["eligible"] and row["barcode_match"]["barcode"] == "00123-AbC"


def test_text_import_lookup_can_opt_out_of_barcode_priority_and_keep_code_semantics(flow):
    f = flow
    # A product code can coincidentally be identical to a valid packaging barcode.
    pack = approved(f)
    add(f, code(external_code="S-NEW", packaging_id=UUID(pack["id"])))
    svc = EntryProducts(f.world.uow)
    barcode = svc.search(f.maker, f.world.entity, "purchase", "S-NEW")["items"][0]
    assert barcode["barcode_match"]["packaging_id"] == pack["id"]
    plain = svc.search(f.maker, f.world.entity, "purchase", "S-NEW", match_barcode=False)["items"][
        0
    ]
    assert plain["id"] == str(f.sku) and plain["barcode_match"] is None
