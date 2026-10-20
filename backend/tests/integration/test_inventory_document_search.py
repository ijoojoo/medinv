from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_location_moves import moved as moved
from test_openings import opened as opened
from test_procurement import flow as flow
from test_quality import isolated as isolated
from test_sales import sale as sale
from test_stock_counts import counted as counted
from test_transfers import transferred as transferred

from medinv.modules.procurement.application.service import ZONE
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture(params=["quality", "move", "transfer", "count", "opening", "ledger"])
def inventory_documents(request):
    kind = request.param
    fixtures = {
        "quality": ("isolated", "quality-dispositions", "quality_disposition"),
        "move": ("moved", "location-moves", "location_move"),
        "transfer": ("transferred", "transfers", "transfer"),
        "count": ("counted", "stock-counts", "stock_count"),
        "opening": ("opened", "inventory-openings", "inventory_opening"),
        "ledger": ("flow", "inventory/ledger", "stock_ledger"),
    }
    fixture, path, table = fixtures[kind]
    x = request.getfixturevalue(fixture)
    if kind == "ledger":
        f = x
        f.post(f.inspect(f.receive()["id"]))
        query = f.service.ledger
        first = query(f.maker, f.world.entity, 0)["items"][0]
        state, term, timestamp = first["event_type"], first["posting_id"], "posted_at"
    else:
        f = x.x.f if kind == "quality" else x.f
        identifier = str(x.create())
        query = x.service.listing
        first = next(r for r in query(f.maker, f.world.entity, 0)["items"] if r["id"] == identifier)
        state, term, timestamp = first["state"], first["number"], "created_at"
    return SimpleNamespace(**locals())


def test_inventory_queries_apply_real_filters_with_unchanged_scope_and_http_validation(
    api, inventory_documents
):
    d = inventory_documents
    f, w = d.f, d.f.world
    day = datetime.fromisoformat(d.first[d.timestamp]).astimezone(ZONE).date()
    criteria = dict(search="  " + d.term.lower() + "  ", state=d.state, date_from=day, date_to=day)
    def query(**kw):
        return d.query(f.maker, w.entity, 0, **kw)
    result = query(**criteria)
    assert [r["id"] for r in result["items"]] == [d.first["id"]]
    assert not result["has_more"]
    for changed in [
        {"date_from": day + timedelta(days=1), "date_to": None},
        {"date_to": day - timedelta(days=1), "date_from": None},
        {"search": "%"},
        {"search": "_' OR 1=1 --"},
    ]:
        assert not query(**(criteria | changed))["items"]
    # Warehouse names remain searchable even for historical documents.
    warehouse = d.first.get("warehouse_name", d.first.get("source_name"))
    assert d.first["id"] in {r["id"] for r in query(search=warehouse)["items"]}
    if d.kind == "quality":
        assert query(**criteria, kind="RELEASE")["items"]
        assert not query(**criteria, kind="WRITE_OFF")["items"]
        with pytest.raises(BusinessError):
            query(kind="invalid")
    for ctx in [
        replace(f.maker, permissions=frozenset()),
        replace(f.maker, entity_ids=frozenset()),
    ]:
        with pytest.raises(BusinessError):
            d.query(ctx, w.entity, 0, **criteria)
    assert not d.query(replace(f.maker, tenant_id=uuid4()), w.entity, 0, **criteria)["items"]
    for invalid in [
        dict(state="UNKNOWN"),
        dict(search="x" * 101),
        dict(date_from=day, date_to=day - timedelta(days=1)),
        dict(date_to=date.max),
    ]:
        with pytest.raises(BusinessError) as exc:
            query(**invalid)
        assert exc.value.code == "INVALID_QUERY"

    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:p WHERE tenant_id=:t"),
            {"p": list(f.maker.permissions), "t": f.maker.tenant_id},
        )
    path = "/api/v1/" + d.path
    params = {"entity_id": str(w.entity), **criteria}
    response = client.get(path, params=params)
    assert response.status_code == 200, response.json()
    assert response.json()["data"] == result
    for invalid in [{"state": "UNKNOWN"}, {"date_from": "bad"}, {"search": "a" * 101}]:
        assert client.get(path, params=params | invalid).status_code == 422
    if d.kind == "quality":
        assert client.get(path, params=params | {"kind": "WRITE_OFF"}).json()["data"]["items"] == []
    published = client.get("/openapi.json").json()["paths"][path]["get"]["parameters"]
    assert {"search", "state", "date_from", "date_to"} <= {p["name"] for p in published}


def test_inventory_filters_run_before_pagination_and_use_shanghai_midnight(transferred):
    x, w = transferred, transferred.w
    identifier = x.create()
    # Isolated query fixture; original lines, balances and posting are not changed.
    w.execute(
        """INSERT INTO med.transfer
        SELECT (jsonb_populate_record(NULL::med.transfer,
          to_jsonb(d)||jsonb_build_object('id',gen_random_uuid(),
            'number','FILTER-'||lpad(n::text,3,'0'),
            'created_at','2030-05-07 15:59:59.999999+00'))).*
        FROM med.transfer d CROSS JOIN generate_series(1,51) n WHERE d.id=:id""",
        id=identifier,
    )
    w.execute(
        "UPDATE med.transfer SET created_at='2030-05-07 16:00:00+00' WHERE id=:id", id=identifier
    )
    filters = dict(
        search="FILTER-", state="DRAFT", date_from=date(2030, 5, 7), date_to=date(2030, 5, 7)
    )
    a = x.service.listing(x.f.maker, w.entity, 0, **filters)
    b = x.service.listing(x.f.maker, w.entity, 50, **filters)
    assert len(a["items"]) == 50 and a["has_more"]
    assert len(b["items"]) == 1 and not b["has_more"]
    assert len({r["id"] for r in a["items"] + b["items"]}) == 51
    assert not x.service.listing(x.f.maker, w.entity, 100, **filters)["items"]
    next_day = x.service.listing(x.f.maker, w.entity, 0, date_from=date(2030, 5, 8))
    assert [r["id"] for r in next_day["items"]] == [str(identifier)]
