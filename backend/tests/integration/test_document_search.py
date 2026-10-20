from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.procurement.application.service import ZONE
from medinv.modules.retail.api.schemas import RetailCreate
from medinv.modules.retail.application.service import RetailService
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture(params=["purchase", "sales", "retail"])
def documents(sale, request):
    s, f = sale, sale.f
    kind = request.param
    if kind == "purchase":
        query, table, path = f.service.orders, "purchase_order", "purchase-orders"
        first, second = f.oid, f.order(approve=False)
        state = "RECEIVED"
    elif kind == "sales":
        query, table, path = s.service.orders, "sales_order", "sales-orders"
        first, second = s.oid, s.order(approve=False)
        state = "APPROVED"
    else:
        service = RetailService(f.world.uow)
        query, table, path = service.listing, "retail_sale", "retail/sales"
        ids = [
            UUID(
                service.create(
                    f.maker,
                    RetailCreate(
                        client_operation_id=uuid4(),
                        entity_id=f.world.entity,
                        warehouse_id=f.world.warehouse,
                        lines=[
                            {"sku_id": f.sku, "qty": "1", "unit_price": "20", "tax_rate": "0.13"}
                        ],
                    ),
                    str(uuid4()),
                )["id"]
            )
            for _ in range(2)
        ]
        first, second = ids
        state = "RESERVED"
    return SimpleNamespace(**locals())


def test_search_combines_literal_text_status_and_shanghai_dates(documents):
    d, f = documents, documents.f

    def query(**kw):
        return d.query(f.maker, f.world.entity, 0, **kw)["items"]

    all_rows = query()
    first = next(r for r in all_rows if r["id"] == str(d.first))
    assert [
        r["id"] for r in query(search="  " + first["number"].lower() + "  ", state=d.state)
    ] == [str(d.first)]
    assert query(search=first["number"], state="CANCELLED") == []
    assert query(search="%' OR 1=1 --") == []
    assert query(search="%") == []
    assert query(search="_") == []
    if d.kind != "retail":
        assert len(query(search="测试供应商")) == 2
        assert len(query(search="sup-new")) == 2
        assert len(query(warehouse_id=f.world.warehouse, party_id=UUID(f.party["id"]))) == 2
        assert query(warehouse_id=uuid4()) == []
        assert query(party_id=uuid4()) == []
    day = datetime.fromisoformat(first["created_at"]).astimezone(ZONE).date()
    assert [r["id"] for r in query(search=first["number"], date_from=day, date_to=day)] == [
        str(d.first)
    ]
    assert query(search=first["number"], date_from=day + timedelta(days=1)) == []
    assert query(search=first["number"], date_to=day - timedelta(days=1)) == []


def test_filtered_lists_keep_permission_entity_and_tenant_boundaries(documents):
    d, f = documents, documents.f
    for ctx in [
        replace(f.maker, permissions=frozenset()),
        replace(f.maker, entity_ids=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            d.query(ctx, f.world.entity, 0, state=d.state)
    foreign = d.query(replace(f.maker, tenant_id=uuid4()), f.world.entity, 0, state=d.state)
    assert foreign == {"items": [], "has_more": False}
    for criteria in [
        {"state": "UNKNOWN"},
        {"search": "a" * 101},
        {"date_from": date(2030, 5, 8), "date_to": date(2030, 5, 7)},
        {"date_to": date.max},
    ]:
        with pytest.raises(BusinessError) as exc:
            d.query(f.maker, f.world.entity, 0, **criteria)
        assert exc.value.code == "INVALID_QUERY"


def test_http_filters_are_documented_validated_and_applied(api, documents):
    client, owner = api
    d, f = documents, documents.f
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:p WHERE tenant_id=:t"),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id},
        )
    path = "/api/v1/" + d.path
    first = next(r for r in d.query(f.maker, f.world.entity, 0)["items"] if r["id"] == str(d.first))
    day = datetime.fromisoformat(first["created_at"]).astimezone(ZONE).date()
    params = {
        "entity_id": str(f.world.entity),
        "search": first["number"],
        "date_from": str(day),
        "date_to": str(day),
        "state": d.state,
    }
    response = client.get(path, params=params)
    assert response.status_code == 200, response.json()
    assert [r["id"] for r in response.json()["data"]["items"]] == [str(d.first)]
    for invalid in [
        {"state": "OTHER"},
        {"search": "x" * 101},
        {"date_from": "bad"},
        {"date_from": str(day + timedelta(days=1))},
    ]:
        assert client.get(path, params={**params, **invalid}).status_code == 422
    published = client.get("/openapi.json").json()["paths"][path]["get"]["parameters"]
    assert {"search", "state", "date_from", "date_to"} <= {p["name"] for p in published}


def test_date_filter_includes_last_microsecond_but_excludes_next_midnight(flow):
    f = flow
    second = f.order(approve=False)
    for identifier, timestamp in [
        (f.oid, "2030-05-07 15:59:59.999999+00"),
        (second, "2030-05-07 16:00:00+00"),
    ]:
        f.world.execute(
            "UPDATE med.purchase_order SET created_at=CAST(:stamp AS timestamptz) WHERE id=:id",
            stamp=timestamp,
            id=identifier,
        )
    day = date(2030, 5, 7)
    result = f.service.orders(f.maker, f.world.entity, 0, date_from=day, date_to=day)
    assert [r["id"] for r in result["items"]] == [str(f.oid)]
    result = f.service.orders(f.maker, f.world.entity, 0, date_from=date(2030, 5, 8))
    assert [r["id"] for r in result["items"]] == [str(second)]


def test_filter_runs_before_pagination_and_has_more_counts_only_matches(flow):
    f = flow
    # Test-only fixture rows; no stock or accounting mutations are needed for list pagination.
    f.world.execute(
        """
        INSERT INTO med.purchase_order
        SELECT (jsonb_populate_record(NULL::med.purchase_order,
          to_jsonb(o)||jsonb_build_object('id',gen_random_uuid(),
            'number','FILTER-'||lpad(n::text,3,'0')))).*
        FROM med.purchase_order o CROSS JOIN generate_series(1,51) n WHERE o.id=:id
    """,
        id=f.oid,
    )
    first = f.service.orders(f.maker, f.world.entity, 0, search="filter-", state="OPEN")
    second = f.service.orders(f.maker, f.world.entity, 50, search="filter-", state="OPEN")
    assert len(first["items"]) == 50 and first["has_more"]
    assert len(second["items"]) == 1 and not second["has_more"]
    assert len({r["id"] for r in first["items"] + second["items"]}) == 51
    assert all(r["number"].startswith("FILTER-") for r in first["items"] + second["items"])
    assert f.service.orders(f.maker, f.world.entity, 100, search="filter-") == {
        "items": [],
        "has_more": False,
    }
