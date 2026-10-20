from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow

from medinv.modules.catalog.api.schemas import PartyCreate
from medinv.modules.catalog.application.party_lookup import PartyLookup
from medinv.modules.catalog.application.service import CatalogService
from medinv.modules.procurement.application.service import ZONE
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_party_lookup_scopes_and_qualifications(flow):
    f = flow
    lookup = PartyLookup(f.world.uow)
    user = replace(f.maker, permissions=frozenset({"purchase.read"}))
    result = lookup.search(user, f.world.entity, "purchase", "sup-new")
    assert len(result["items"]) == 1
    assert result["items"][0]["eligible"] is True
    assert result["items"][0]["code"] == "SUP-NEW"
    assert lookup.search(user, f.world.entity, "purchase", "%' OR 1=1 --")["items"] == []
    assert lookup.search(user, f.world.entity, "purchase", party_id=uuid4())["items"] == []
    assert (
        lookup.search(replace(user, tenant_id=uuid4()), f.world.entity, "purchase")["items"] == []
    )
    for ctx in [replace(user, permissions=frozenset()), replace(user, entity_ids=frozenset())]:
        with pytest.raises(BusinessError):
            lookup.search(ctx, f.world.entity, "purchase")
    with pytest.raises(BusinessError):
        lookup.search(user, f.world.entity, "sale")
    assert (
        f.service.options(user, f.world.entity, include_skus=False, include_parties=False)[
            "suppliers"
        ]
        == []
    )


def test_party_lookup_paginates_history_without_offering_unapproved_parties(flow):
    f = flow
    catalog = CatalogService(f.world.uow)
    lookup = PartyLookup(f.world.uow)
    day = datetime.now(ZONE).date()
    ids = []
    for n in range(21):
        created = catalog.create_party(
            f.maker,
            PartyCreate(
                entity_id=f.world.entity,
                code=f"PAGE-{n:02}",
                name=f"分页往来-{n:02}",
                credit_code=f"LOOKUP-{n:02}",
                kind="BOTH",
                license_no="TEST",
                valid_from=day,
                valid_until=day + timedelta(days=30),
                categories=["DEVICE"],
            ),
            str(uuid4()),
        )
        ids.append(created["id"])
    assert lookup.search(f.maker, f.world.entity, "sale", "分页往来")["items"] == []
    first = lookup.search(f.maker, f.world.entity, "sale", "分页往来", qualified_only=False)
    second = lookup.search(f.maker, f.world.entity, "sale", "分页往来", 20, qualified_only=False)
    assert len(first["items"]) == 20 and first["has_more"]
    assert len(second["items"]) == 1 and not second["has_more"]
    assert {r["id"] for r in first["items"] + second["items"]} == set(ids)
    exact = lookup.search(f.maker, f.world.entity, "sale", party_id=UUID(ids[0]))
    assert exact["items"][0]["eligible"] is False
    assert (
        lookup.search(f.maker, f.world.entity, "sale", "LOOKUP-20", qualified_only=False)["items"][
            0
        ]["id"]
        == ids[20]
    )
