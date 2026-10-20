from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow
from test_sales import sale as sale

from medinv.modules.procurement.api.schemas import OrderCreate, OrderRevision
from medinv.modules.procurement.application import amendments as purchasing
from medinv.modules.sales.api.schemas import SalesCreate, SalesRevision
from medinv.modules.sales.application import amendments as selling
from medinv.modules.workspace.drafts import DocumentDrafts, DraftDiscard, DraftSave
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def payload(world, channel="purchase", **extra):
    return DraftSave.model_validate(
        {
            "channel": channel,
            "entity_id": str(world.entity),
            "expected_version": "0",
            "content": {"lines": [{"client_key": str(uuid4()), "qty": "尚未填写"}]},
            **extra,
        }
    )


def test_incomplete_drafts_are_private_versioned_and_have_no_business_effect(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    svc, did = DocumentDrafts(world.uow), uuid4()
    before = world.scalar("SELECT count(*) FROM med.purchase_order")
    body, key = payload(world), str(uuid4())
    created = svc.save(ctx, did, body, key)
    assert created["version"] == "1" and created["state"] == "EDITING"
    assert svc.save(ctx, did, body, key) == created
    assert world.scalar("SELECT count(*) FROM med.purchase_order") == before
    assert svc.listing(ctx, "purchase", world.entity, 0)["items"][0]["id"] == str(did)
    for outsider in [replace(ctx, principal_id=uuid4()), replace(ctx, tenant_id=uuid4())]:
        with pytest.raises(BusinessError) as exc:
            svc.detail(outsider, did)
        assert exc.value.status == 404
    for denied in [replace(ctx, permissions=frozenset()), replace(ctx, entity_ids=frozenset())]:
        with pytest.raises(BusinessError):
            svc.detail(denied, did)
    changed = body.model_copy(update={"expected_version": "1", "title": "第二窗口修改"})
    assert svc.save(ctx, did, changed, str(uuid4()))["version"] == "2"
    with pytest.raises(BusinessError, match="其他窗口"):
        svc.save(ctx, did, changed, str(uuid4()))
    assert svc.detail(ctx, did)["title"] == "第二窗口修改"
    svc.discard(ctx, did, DraftDiscard(expected_version="2"), str(uuid4()))
    assert svc.listing(ctx, "purchase", world.entity, 0)["items"] == []
    with pytest.raises(BusinessError, match="放弃"):
        svc.save(ctx, did, changed.model_copy(update={"expected_version": "3"}), str(uuid4()))


@pytest.mark.parametrize("channel", ["purchase", "sale"])
def test_conversion_is_atomic_and_cannot_duplicate_after_lost_response(sale, channel):
    s, f = sale, sale.f
    w = f.world
    svc, did = DocumentDrafts(w.uow), uuid4()
    svc.save(f.maker, did, payload(w, channel), str(uuid4()))
    fields = {
        "entity_id": w.entity,
        "warehouse_id": w.warehouse,
        "lines": [{"sku_id": f.sku, "qty": "2", "unit_price": "3", "tax_rate": "0.13"}],
        "editor_draft": {"id": did, "version": "1"},
    }
    if channel == "purchase":
        body = OrderCreate(**fields, supplier_id=f.party["id"])
        create = f.service.create_order
        table = "purchase_order"
    else:
        body = SalesCreate(**fields, customer_id=s.customer, delivery_address="测试地址")
        create = s.service.create
        table = "sales_order"
    before = w.scalar(f"SELECT count(*) FROM med.{table}")
    bad = body.model_copy(update={"warehouse_id": uuid4()})
    with pytest.raises(BusinessError):
        create(f.maker, bad, str(uuid4()))
    assert svc.detail(f.maker, did)["state"] == "EDITING"
    key = str(uuid4())
    result = create(f.maker, body, key)
    assert create(f.maker, body, key) == result
    assert svc.detail(f.maker, did)["converted_order_id"] == result["id"]
    with pytest.raises(BusinessError, match="已转单"):
        create(f.maker, body, str(uuid4()))
    assert w.scalar(f"SELECT count(*) FROM med.{table}") == before + 1


@pytest.mark.parametrize("channel", ["purchase", "sale"])
def test_revision_draft_is_bound_to_original_document_and_version(sale, channel):
    s, f = sale, sale.f
    w = f.world
    service = f.service if channel == "purchase" else s.service
    oid = f.order(approve=False) if channel == "purchase" else s.order(approve=False)
    detail = service.detail(f.maker, oid)
    version = detail["order"]["version"]
    did, drafts = uuid4(), DocumentDrafts(w.uow)
    drafts.save(
        f.maker,
        did,
        payload(w, channel, source_order_id=oid, source_version=version),
        str(uuid4()),
    )
    fields = {
        "warehouse_id": w.warehouse,
        "expected_version": version,
        "reason": "测试修改",
        "lines": [{"sku_id": f.sku, "qty": "2", "unit_price": "3", "tax_rate": "0.13"}],
        "editor_draft": {"id": did, "version": "1"},
    }
    if channel == "purchase":
        body = OrderRevision(**fields, supplier_id=f.party["id"])
        revise = purchasing.revise
    else:
        body = SalesRevision(**fields, customer_id=s.customer, delivery_address="测试地址")
        revise = selling.revise
    other = f.order(approve=False) if channel == "purchase" else s.order(approve=False)
    with pytest.raises(BusinessError, match="不匹配"):
        revise(service, f.maker, other, body, str(uuid4()))
    assert service.detail(f.maker, other)["order"]["version"] == version
    assert drafts.detail(f.maker, did)["state"] == "EDITING"
    revise(service, f.maker, oid, body, str(uuid4()))
    assert drafts.detail(f.maker, did)["converted_order_id"] == str(oid)
    assert service.detail(f.maker, oid)["lines"][0]["qty"].startswith("2")


def test_optional_link_preserves_legacy_command_fingerprint(world):
    body = OrderCreate(
        entity_id=world.entity,
        supplier_id=uuid4(),
        warehouse_id=world.warehouse,
        lines=[{"sku_id": world.sku, "qty": "1", "unit_price": "2"}],
    )
    assert "editor_draft" not in body.model_dump(mode="json")
    linked = body.model_copy(update={"editor_draft": None})
    assert linked.model_dump(mode="json") == body.model_dump(mode="json")


def test_draft_search_pages_both_channels_and_matches_saved_business_context(flow):
    f, service = flow, DocumentDrafts(flow.world.uow)
    ids = []
    for index, channel in enumerate(["purchase", "sale", "purchase", "sale", "purchase"]):
        did = uuid4()
        service.save(f.maker, did, payload(f.world, channel, title=f"待处理 {index}"), str(uuid4()))
        ids.append(str(did))
    first = service.listing(f.maker, "all", f.world.entity, 0, limit=2)
    second = service.listing(f.maker, "all", f.world.entity, 2, limit=2)
    last = service.listing(f.maker, "all", f.world.entity, 4, limit=2)
    assert first["total"] == second["total"] == last["total"] == 5
    assert first["has_more"] and second["has_more"] and not last["has_more"]
    assert [d["id"] for page in [first, second, last] for d in page["items"]] == ids[::-1]
    assert service.listing(f.maker, "all", f.world.entity, 20, limit=2) == {
        "items": [],
        "total": 5,
        "has_more": False,
    }
    found = service.listing(f.maker, "all", f.world.entity, 0, search="  待处理 0  ")
    assert [d["id"] for d in found["items"]] == ids[:1]
    did = uuid4()
    source = f.detail()["order"]
    body = payload(
        f.world,
        title="调货 100%_AbC",
        source_order_id=f.oid,
        source_version=source["version"],
        content={
            "supplier_id": f.party["id"].upper(),
            "warehouse_id": str(f.world.warehouse).upper(),
            "lines": [
                {"client_key": str(uuid4()), "sku_id": str(f.sku)},
                {"client_key": str(uuid4())},
            ],
        },
    )
    service.save(f.maker, did, body, str(uuid4()))
    for query in ["100%_abc", "测试供应商", "测试仓库", source["number"]]:
        result = service.listing(f.maker, "all", f.world.entity, 0, search=query)
        assert result["total"] == 1
        row = result["items"][0]
        assert row["id"] == str(did) and row["line_count"] == 1
        assert row["party_name"] == "测试供应商" and row["warehouse_name"] == "测试仓库"
        assert row["source_number"] == source["number"] and row["version"] == "1"
        assert "content" not in row and "created_by" not in row
    # Draft references may be incomplete, invalid UUID strings or point to an absent record.
    service.save(
        f.maker,
        uuid4(),
        payload(
            f.world,
            content={
                "supplier_id": "a" * 36,
                "warehouse_id": str(uuid4()),
                "lines": [],
            },
        ),
        str(uuid4()),
    )
    row = service.listing(f.maker, "all", f.world.entity, 0, limit=1)["items"][0]
    assert row["party_name"] == row["warehouse_name"] == "" and row["line_count"] == 0


def test_draft_all_listing_enforces_actor_tenant_entity_and_channel_permissions(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    svc = DocumentDrafts(world.uow)
    for channel in ["purchase", "sale"]:
        svc.save(ctx, uuid4(), payload(world, channel), str(uuid4()))
    assert svc.listing(ctx, "all", world.entity, 0)["total"] == 2
    for permission, channel in [("purchase.write", "purchase"), ("sales.write", "sale")]:
        limited = replace(ctx, permissions=frozenset([permission]))
        found = svc.listing(limited, "all", world.entity, 0)
        assert found["total"] == 1 and found["items"][0]["channel"] == channel
        with pytest.raises(BusinessError) as error:
            svc.listing(limited, "sale" if channel == "purchase" else "purchase", world.entity, 0)
        assert error.value.status == 403
    assert svc.listing(replace(ctx, principal_id=uuid4()), "all", world.entity, 0)["total"] == 0
    for other, status in [
        (replace(ctx, permissions=frozenset()), 403),
        (replace(ctx, entity_ids=frozenset()), 404),
    ]:
        with pytest.raises(BusinessError) as error:
            svc.listing(other, "all", world.entity, 0)
        assert error.value.status == status
    with pytest.raises(BusinessError) as error:
        svc.listing(replace(ctx, tenant_id=uuid4()), "all", world.entity, 0)
    assert error.value.status == 404
    missing = uuid4()
    with pytest.raises(BusinessError) as error:
        svc.listing(replace(ctx, entity_ids=frozenset([missing])), "all", missing, 0)
    assert error.value.status == 404


def test_draft_listing_http_defaults_bounds_and_no_store(api, world):
    client, owner = api
    params = {"entity_id": str(world.entity)}
    url = "/api/v1/document-drafts"
    assert client.get(url, params=params).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("""UPDATE control.membership SET permissions=ARRAY['purchase.write']
                         WHERE tenant_id=:t AND principal_id=:a"""),
            {"t": world.ctx.tenant_id, "a": world.ctx.principal_id},
        )
    result = client.get(url, params=params)
    assert result.status_code == 200, result.text
    assert result.json()["data"] == {"items": [], "total": 0, "has_more": False}
    assert result.headers["Cache-Control"] == "no-store"
    for extra in [
        {"channel": "bad"},
        {"offset": -1},
        {"limit": 0},
        {"limit": 51},
        {"search": "长" * 101},
    ]:
        assert client.get(url, params={**params, **extra}).status_code == 422
    assert client.get(url, params={**params, "channel": "sale"}).status_code == 403
