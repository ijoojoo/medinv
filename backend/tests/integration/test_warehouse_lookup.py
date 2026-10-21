from dataclasses import replace
from uuid import uuid4

import pytest
from test_api import api as api

from medinv.modules.inventory.application.warehouse_lookup import WarehouseLookup
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "channel,permission", [("purchase", "purchase.read"), ("sale", "sales.read")]
)
def test_lookup_is_scoped_and_does_not_require_inventory_permission(world, channel, permission):
    lookup = WarehouseLookup(world.uow)
    user = replace(world.ctx, permissions=frozenset({permission}))
    row = lookup.search(user, world.entity, channel, "wh1")["items"][0]
    assert row == {"id": str(world.warehouse), "name": "测试仓库", "code": "WH1", "eligible": True}
    assert lookup.search(user, world.entity, channel, "测试")["items"] == [row]
    assert lookup.search(user, world.entity, channel, "%' OR 1=1 --")["items"] == []
    assert lookup.search(user, world.entity, channel, warehouse_id=uuid4())["items"] == []
    assert lookup.search(replace(user, tenant_id=uuid4()), world.entity, channel)["items"] == []
    for denied in [replace(user, permissions=frozenset()), replace(user, entity_ids=frozenset())]:
        with pytest.raises(BusinessError) as failure:
            lookup.search(denied, world.entity, channel)
        assert failure.value.status in {403, 404}
    other_entity = uuid4()
    allowed = replace(user, entity_ids=frozenset({world.entity, other_entity}))
    assert (
        lookup.search(allowed, other_entity, channel, warehouse_id=world.warehouse)["items"] == []
    )
    with pytest.raises(BusinessError):
        lookup.search(user, world.entity, "sale" if channel == "purchase" else "purchase")


def test_lookup_pages_same_name_warehouses_and_excludes_archived_and_system(world):
    lookup = WarehouseLookup(world.uow)
    user = replace(world.ctx, permissions=frozenset({"purchase.read"}))
    world.execute(
        """INSERT INTO med.warehouse SELECT (jsonb_populate_record(NULL::med.warehouse,
        to_jsonb(w)||jsonb_build_object('id',gen_random_uuid(),
          'code','PAGE-'||lpad(n::text,2,'0'),'name','同名测试仓库'))).*
        FROM med.warehouse w CROSS JOIN generate_series(1,22) n WHERE w.id=:id""",
        id=world.warehouse,
    )
    first = lookup.search(user, world.entity, "purchase", "同名")
    second = lookup.search(user, world.entity, "purchase", "同名", 20)
    assert len(first["items"]) == 20 and first["has_more"]
    assert len(second["items"]) == 2 and not second["has_more"]
    assert [r["code"] for r in first["items"] + second["items"]] == [
        f"PAGE-{n:02}" for n in range(1, 23)
    ]
    archived_id = second["items"][1]["id"]
    world.execute("UPDATE med.warehouse SET archived_at=now() WHERE id=:id", id=archived_id)
    assert lookup.search(user, world.entity, "purchase", "PAGE-22")["items"] == []
    archived = lookup.search(user, world.entity, "purchase", warehouse_id=archived_id)["items"][0]
    assert archived["name"] == "同名测试仓库" and not archived["eligible"]
    # A system transit warehouse may share the name, but is never an entry choice.
    world.execute("UPDATE med.warehouse SET kind='TRANSIT' WHERE code='PAGE-21'")
    assert lookup.search(user, world.entity, "purchase", "PAGE-21")["items"] == []
    assert (
        lookup.search(user, world.entity, "purchase", warehouse_id=second["items"][0]["id"])[
            "items"
        ]
        == []
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"offset": -1},
        {"offset": 100001},
        {"search": "x" * 101},
        {"channel": "retail"},
        {"warehouse_id": "invalid"},
    ],
)
def test_lookup_http_validation_and_static_route_priority(api, world, invalid):
    client, _ = api
    response = client.get(
        "/api/v1/warehouses/lookup",
        params={"entity_id": str(world.entity), "channel": "purchase", **invalid},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_lookup_http_has_business_permission_boundary(api, world):
    client, _ = api
    response = client.get(
        "/api/v1/warehouses/lookup", params={"entity_id": str(world.entity), "channel": "purchase"}
    )
    assert response.status_code == 403
