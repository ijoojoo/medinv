from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_api import api as api
from test_procurement import flow as flow

from medinv.modules.sales.application.service import SalesService
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def large_catalog(flow):
    f = flow
    f.world.execute("UPDATE med.party SET kind='BOTH'")
    f.world.execute(
        """
      INSERT INTO med.party SELECT (jsonb_populate_record(NULL::med.party,
        to_jsonb(p)||jsonb_build_object('id',gen_random_uuid(),
          'code','PAGE-'||n,'name','分页单位'||lpad(n::text,3,'0')))).*
      FROM med.party p CROSS JOIN generate_series(1,205) n WHERE p.id=:id
    """,
        id=UUID(f.party["id"]),
    )
    f.world.execute(
        """
      INSERT INTO med.sku SELECT (jsonb_populate_record(NULL::med.sku,
        to_jsonb(s)||jsonb_build_object('id',gen_random_uuid(),'code','PAGE-'||n))).*
      FROM med.sku s CROSS JOIN generate_series(1,205) n WHERE s.id=:id
    """,
        id=f.sku,
    )
    f.world.execute(
        """
      INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id)
      SELECT tenant_id,gen_random_uuid(),created_by,updated_by,:e,id FROM med.sku
      WHERE code LIKE 'PAGE-%'
    """,
        e=f.world.entity,
    )
    return f


@pytest.mark.parametrize("kind", ["purchase", "sales"])
def test_options_over_200_are_bounded_pageable_complete_and_tenant_scoped(large_catalog, kind):
    f = large_catalog
    svc = f.service if kind == "purchase" else SalesService(f.world.uow)
    field = "suppliers" if kind == "purchase" else "customers"
    first = svc.options(f.maker, f.world.entity)
    assert len(first[field]) == len(first["skus"]) == 200
    assert first["has_more"] and first["next_offset"] == 200
    second = svc.options(f.maker, f.world.entity, first["next_offset"])
    assert len(second[field]) == len(second["skus"]) == 6
    assert not second["has_more"] and second["next_offset"] is None
    for key in [field, "skus"]:
        assert len({item["id"] for item in first[key] + second[key]}) == 206
    assert len(first["warehouses"]) == 1 and second["warehouses"] == []
    assert svc.options(f.maker, f.world.entity, 400)["skus"] == []
    foreign = svc.options(replace(f.maker, tenant_id=uuid4()), f.world.entity)
    assert foreign[field] == foreign["skus"] == []
    assert not foreign["has_more"]
    with pytest.raises(BusinessError):
        svc.options(replace(f.maker, entity_ids=frozenset()), f.world.entity, 200)


@pytest.mark.parametrize("path", ["purchasing", "selling", "inventory"])
def test_options_http_pagination_bounds_are_validated(api, world, path):
    client, _ = api
    for offset in [-1, 100001]:
        response = client.get(
            f"/api/v1/{path}/options", params={"entity_id": str(world.entity), "offset": offset}
        )
        assert response.status_code == 422
    schema = client.get("/openapi.json").json()
    params = schema["paths"][f"/api/v1/{path}/options"]["get"]["parameters"]
    assert any(p["name"] == "offset" and p["schema"]["maximum"] == 100000 for p in params)


def test_inventory_options_paginate_stock_skus_and_warehouses_without_cross_tenant_leaks(world):
    world.execute(
        """
      INSERT INTO med.sku SELECT (jsonb_populate_record(NULL::med.sku,
        to_jsonb(s)||jsonb_build_object('id',gen_random_uuid(),'code','PAGE-'||n))).*
      FROM med.sku s CROSS JOIN generate_series(1,205) n WHERE s.id=:id
    """,
        id=world.sku,
    )
    world.execute(
        """
      INSERT INTO med.stock_balance
        (tenant_id,id,created_by,updated_by,entity_id,owner_id,warehouse_id,
         location_id,sku_id,quality_status)
      SELECT tenant_id,gen_random_uuid(),created_by,updated_by,:e,:e,:w,:l,id,'QUALIFIED'
      FROM med.sku WHERE code LIKE 'PAGE-%'
    """,
        e=world.entity,
        w=world.warehouse,
        l=world.location,
    )
    world.execute(
        """
      INSERT INTO med.warehouse SELECT (jsonb_populate_record(NULL::med.warehouse,
        to_jsonb(w)||jsonb_build_object('id',gen_random_uuid(),
          'code','PAGE-'||n,'name','分页仓库'||lpad(n::text,3,'0')))).*
      FROM med.warehouse w CROSS JOIN generate_series(1,205) n WHERE w.id=:id
    """,
        id=world.warehouse,
    )
    first = world.service.options(world.ctx, world.entity)
    second = world.service.options(world.ctx, world.entity, first["next_offset"])
    assert first["has_more"] and first["next_offset"] == 200
    assert not second["has_more"] and second["next_offset"] is None
    for field in ["warehouses", "skus"]:
        assert len(first[field]) == 200 and len(second[field]) == 6
        assert len({item["id"] for item in first[field] + second[field]}) == 206
    assert world.service.options(world.ctx, world.entity, 400)["skus"] == []
    foreign = world.service.options(replace(world.ctx, tenant_id=uuid4()), world.entity)
    assert foreign["skus"] == foreign["warehouses"] == [] and not foreign["has_more"]
    with pytest.raises(BusinessError):
        world.service.options(replace(world.ctx, entity_ids=frozenset()), world.entity, 200)
