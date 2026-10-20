from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from test_procurement import flow as flow

from medinv.modules.procurement.application.workflow import worklist
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def query(f, stage, view="pending", ctx=None, **kwargs):
    return worklist(f.world.uow, ctx or f.reviewer, f.world.entity, stage, view, **kwargs)


def test_worklists_follow_partial_receipt_inspection_and_posting(flow):
    f = flow
    assert query(f, "receiving")["items"][0]["state"] == "WAITING"
    assert query(f, "inspection")["items"] == []
    receipt = f.receive("9")
    waiting = query(f, "receiving")["items"][0]
    assert waiting["state"] == "PARTIAL"
    assert Decimal(waiting["quantities"][0]["pending"]) == 1
    history = query(f, "receiving", "history")["items"]
    assert [r["id"] for r in history] == [receipt["id"]]
    assert history[0]["action"] == ""
    inspected = f.inspect(receipt["id"], "7", "1")
    pending = query(f, "inspection")["items"][0]
    assert pending["state"] == "PARTIAL"
    assert Decimal(pending["quantities"][0]["pending"]) == 1
    assert pending["order_id"] == str(f.oid)
    assert pending["action"] == "inspect"
    own = query(f, "inspection", ctx=f.maker)["items"][0]
    assert own["action"] == "" and own["action_note"]
    inspection = query(f, "inspection", "history")["items"][0]
    assert inspection["id"] == inspected["inspection_id"]
    assert Decimal(inspection["quantities"][0]["rejected"]) == 1
    posting = query(f, "posting")["items"][0]
    assert posting["inspection_id"] == inspection["id"]
    assert posting["segment_id"] == inspected["receipt_posting_id"]
    assert Decimal(posting["quantities"][0]["qualified"]) == 7
    assert query(f, "posting", "history")["items"] == []
    # Original posting idempotency is retained; a retry creates no second record.
    key = str(uuid4())
    f.post(inspected, key)
    f.post(inspected, key)
    assert query(f, "posting")["items"] == []
    posted = query(f, "posting", "history")["items"]
    assert len(posted) == 1 and posted[0]["state"] == "POSTED"
    assert Decimal(posted[0]["quantities"][0]["qualified"]) == 7
    assert query(f, "inspection")["items"]  # Outstanding quantity remains actionable.
    assert query(f, "orders")["items"][0]["state"] == "OPEN"


def test_workflow_filters_pagination_and_access(flow):
    f = flow
    assert len(query(f, "orders", date_from=f.day, date_to=f.day)["items"]) == 1
    assert query(f, "orders", date_to=f.day - timedelta(days=1))["items"] == []
    assert len(query(f, "orders", warehouse_id=f.world.warehouse)["items"]) == 1
    assert query(f, "orders", party_id=uuid4())["items"] == []
    assert query(f, "orders", search="absent")["items"] == []
    assert len(query(f, "orders", search="测试供应商")["items"]) == 1
    assert query(f, "orders", search="' OR TRUE --")["items"] == []
    with pytest.raises(BusinessError):
        query(f, "orders", date_from=f.day, date_to=f.day - timedelta(days=1))
    with pytest.raises(BusinessError):
        query(f, "receiving", state="POSTED")
    with pytest.raises(BusinessError):
        query(f, "orders", ctx=replace(f.maker, permissions=frozenset()))
    with pytest.raises(BusinessError):
        query(f, "orders", ctx=replace(f.maker, entity_ids=frozenset()))
    other_tenant = replace(f.maker, tenant_id=uuid4())
    assert query(f, "orders", ctx=other_tenant)["items"] == []
    read_only = replace(f.maker, permissions=frozenset({"purchase.read"}))
    assert query(f, "receiving", ctx=read_only)["items"][0]["action"] == ""
    for _ in range(50):
        f.order(approve=False)
    page1 = query(f, "orders", sort="oldest")
    page2 = query(f, "orders", sort="oldest", offset=50)
    assert len(page1["items"]) == 50 and page1["has_more"]
    assert len(page2["items"]) == 1 and not page2["has_more"]
    assert not ({r["id"] for r in page1["items"]} & {r["id"] for r in page2["items"]})
