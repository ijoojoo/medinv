from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_returns import ret as ret
from test_sales import sale as sale
from test_settlement import finance as finance

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.returns.api.schemas import CustodyHandover, ReturnReceive
from medinv.modules.returns.application import custody
from medinv.modules.sales.api.schemas import ShipmentCreate
from medinv.modules.sales.application import fulfillment
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def test_selecting_one_priced_line_does_not_allocate_whole_document_money(ret):
    r = ret
    s = r.f.s
    oid = s.order(
        lines=[
            {"sku_id": s.f.sku, "qty": "1", "unit_price": "0.01", "tax_rate": "0.13"},
            {"sku_id": s.f.sku, "qty": "2", "unit_price": "7.49", "tax_rate": "0.13"},
        ]
    )
    doc = s.detail(oid)
    sid = fulfillment.allocate(
        s.service,
        r.f.maker,
        oid,
        ShipmentCreate(
            expected_version=doc["order"]["version"],
            client_operation_id=uuid4(),
            lines=[{"order_line_id": line["id"], "qty": line["qty"]} for line in doc["lines"]],
        ),
        str(uuid4()),
    )["id"]
    s.pick(sid, oid)
    s.verify(sid, oid)
    s.post(sid)
    source = r.f.items()[0]
    rows = r.service.source_detail(r.f.maker, UUID(source["posting_id"]))["lines"]
    line = next(x for x in rows if x["gross_amount"] == "14.98")
    rid = r.create(lines=[{"source_posting_line_id": line["id"], "qty": "1"}])
    assert len(r.detail(rid)["lines"]) == 1
    assert r.detail(rid)["document"]["gross_amount"] == "7.49"
    r.review(rid)
    r.post(rid)
    rid2 = r.ready()
    assert len(r.detail(rid2)["lines"]) == 2
    assert r.detail(rid2)["document"]["gross_amount"] == "7.50"
    r.post(rid2)
    assert r.f.items()[0]["returned_amount"] == "14.99"


def part(r, qty, kind="SALES"):
    source = r.f.items("AR" if kind == "SALES" else "AP")[0]
    line = r.service.source_detail(r.f.maker, UUID(source["posting_id"]))["lines"][0]
    return r.create(kind, lines=[{"source_posting_line_id": line["id"], "qty": qty}])


def test_repeated_sales_returns_close_quantities_money_cost_credit_and_view_once(ret):
    r = ret
    for i in range(3):
        rid = part(r, "1")
        assert r.detail(rid)["document"]["gross_amount"] == "20.00"
        r.review(rid)
        assert r.post(rid)["cost_amount"] == "11.50400000"
        items = r.f.items()
        assert len(items) == 1
        assert Decimal(items[0]["returned_amount"]) == 20 * (i + 1)
        assert items[0]["version"] == str(i + 2)
        assert r.f.s.scalar("on_hand") == 8 + i
    assert r.f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("115.04")
    assert r.f.items()[0]["outstanding_amount"] == "0.00"
    with pytest.raises(BusinessError) as err:
        part(r, "1")
    assert err.value.code == "RETURN_QUANTITY_EXCEEDED"


def test_partial_purchase_rounding_and_final_pool_residual(ret):
    r = ret
    r.post(r.ready())
    net = Decimal(0)
    for qty in ("1", "2", "7"):
        rid = part(r, qty, "PURCHASE")
        net += Decimal(r.detail(rid)["document"]["net_amount"])
        r.review(rid)
        r.post(rid)
    assert net == Decimal("115.04")
    assert r.f.items("AP")[0]["returned_amount"] == "130.00"
    assert r.f.items("AP")[0]["outstanding_amount"] == "0.00"
    assert r.f.s.scalar("on_hand") == 0
    assert r.f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == 0


def test_custody_is_not_owned_stock_and_rejection_requires_physical_handback(ret):
    r = ret
    rid = part(r, "2")
    with pytest.raises(BusinessError):
        r.service.review(r.f.s.f.reviewer, rid, r.review_body(rid), str(uuid4()))
    r.receive(rid)
    assert r.detail(rid)["document"]["state"] == "RECEIVED"
    assert r.f.s.scalar("on_hand") == 7
    assert r.f.items()[0]["outstanding_amount"] == "60.00"
    assert r.f.s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance") == Decimal("80.528")
    with pytest.raises(BusinessError):
        r.f.s.allocate("8")
    assert len(r.service.listing(r.f.maker, r.f.s.f.world.entity, 0, True)["items"]) == 1
    body = r.review_body(rid).model_copy(update={"decision": "REJECT", "lines": []})
    r.service.review(r.f.s.f.reviewer, rid, body, str(uuid4()))
    assert r.detail(rid)["document"]["state"] == "REJECTED"
    with pytest.raises(BusinessError):
        r.service.cancel(
            r.f.maker, rid, Transition(expected_version="3", reason="实物仍保管"), str(uuid4())
        )
    with pytest.raises(BusinessError):
        part(r, "1")
    custody.hand_back(
        r.service,
        r.f.maker,
        rid,
        CustodyHandover(expected_version="3", reason="全部实物交还客户签收", confirmed=True),
        str(uuid4()),
    )
    assert r.detail(rid)["custody"][0]["outcome"] == "HANDED_BACK"
    assert not r.service.listing(r.f.maker, r.f.s.f.world.entity, 0, True)["items"]
    assert r.f.s.scalar("on_hand") == 7
    r.post(r.ready())
    assert r.f.s.scalar("on_hand") == 10


def test_return_can_preserve_allocations_with_sufficient_outstanding_and_refund_cap(ret):
    r = ret
    cid = r.f.cash()["id"]
    r.f.allocate(cid, "40")
    rid = part(r, "1")
    r.review(rid)
    r.post(rid)
    assert r.f.items()[0]["outstanding_amount"] == "0.00"
    assert r.f.items()[0]["settled_amount"] == "40.00"
    r.refund(rid, cid, "20")
    assert r.f.detail(cid)["cash"]["remaining_amount"] == "0.00"
    with pytest.raises(BusinessError):
        r.refund(rid, cid, "1")
    rid2 = part(r, "1")
    r.review(rid2)
    with pytest.raises(BusinessError) as err:
        r.post(rid2)
    assert err.value.code == "RETURN_REVERSE_ALLOCATIONS"
    assert r.detail(rid2)["custody"][0]["outcome"] is None


def test_partial_creation_serializes_competing_operations_and_cancellation_releases(ret):
    r = ret

    def create():
        try:
            return part(r, "2")
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: create(), range(5)))
    assert results.count("RETURN_EXISTS") == 4
    rid = next(x for x in results if isinstance(x, UUID))
    r.service.cancel(
        r.f.maker, rid, Transition(expected_version="1", reason="重新申请"), str(uuid4())
    )
    rid2 = part(r, "3")
    r.review(rid2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        posted = list(pool.map(lambda _: r.post(rid2), range(4)))
    assert all(x == posted[0] for x in posted)
    assert r.f.s.f.world.scalar("SELECT count(*) FROM med.return_custody_close") == 1


def test_receipt_exact_manifest_independent_receiver_and_immutable_custody(ret):
    r = ret
    rid = part(r, "1")
    body = ReturnReceive(
        expected_version="1",
        reason="收货",
        confirmed=True,
        zone_label="隔离区",
        lines=r.review_body(rid).lines,
    )
    body.lines[0].qty = "2.000000"
    with pytest.raises(BusinessError) as err:
        custody.receive(r.service, r.f.maker, rid, body, str(uuid4()))
    assert err.value.code == "RETURN_CHECK_MISMATCH"
    body.lines[0].qty = "1.000000"
    custody.receive(r.service, r.f.s.f.reviewer, rid, body, str(uuid4()))
    with pytest.raises(BusinessError) as err:
        r.service.review(r.f.s.f.reviewer, rid, r.review_body(rid), str(uuid4()))
    assert err.value.code == "MAKER_CHECKER"
    for table in ("return_line", "return_custody"):
        with pytest.raises(DBAPIError):
            r.f.s.f.world.execute(f"DELETE FROM med.{table}")
    with pytest.raises(DBAPIError):
        r.f.s.f.world.execute("UPDATE med.posting_line SET gross_amount=1")


def test_custody_tenant_permissions_scope_and_version(ret):
    r = ret
    rid = part(r, "1")
    body = ReturnReceive(
        expected_version="1",
        reason="收货",
        confirmed=True,
        zone_label="隔离区",
        lines=r.review_body(rid).lines,
    )
    for ctx in (
        replace(r.f.maker, tenant_id=uuid4()),
        replace(r.f.maker, entity_ids=frozenset()),
        replace(r.f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
    ):
        with pytest.raises(BusinessError):
            custody.receive(r.service, ctx, rid, body, str(uuid4()))
    r.receive(rid)
    with pytest.raises(BusinessError) as err:
        custody.hand_back(
            r.service,
            r.f.maker,
            rid,
            CustodyHandover(expected_version="1", confirmed=True, reason="旧版本"),
            str(uuid4()),
        )
    assert err.value.code == "VERSION_CONFLICT"


def test_post_commit_failure_keeps_custody_open_and_original_balances(ret):
    r = ret
    rid = part(r, "2")
    r.review(rid)

    def fail():
        raise BusinessError("SESSION_EXPIRED", "会话失效", 401)

    with before_business_commit.set(fail), pytest.raises(BusinessError):
        r.post(rid)
    assert r.detail(rid)["custody"][0]["outcome"] is None
    assert r.f.s.scalar("on_hand") == 7
    assert r.f.s.f.world.scalar("SELECT count(*) FROM med.return_custody_close") == 0
    assert r.f.items()[0]["returned_amount"] == "0.00"


def test_invalid_fraction_foreign_line_and_excess_quantity(ret):
    r = ret
    for qty, code in (("0.5", "RETURN_QUANTITY_INVALID"), ("4", "RETURN_QUANTITY_EXCEEDED")):
        with pytest.raises(BusinessError) as err:
            part(r, qty)
        assert err.value.code == code
    with pytest.raises(BusinessError) as err:
        r.create(lines=[{"source_posting_line_id": uuid4(), "qty": "1"}])
    assert err.value.code == "RETURN_LINE_INVALID"


def test_legacy_source_full_only_and_migrated_draft_still_posts(ret, monkeypatch):
    r = ret
    execute = SqlSession.execute

    def legacy(self, sql, params):
        if "INSERT INTO med.posting_line(" in sql:
            params = {**params, "gross": None, "net": None}
        return execute(self, sql, params)

    with monkeypatch.context() as patch:
        patch.setattr(SqlSession, "execute", legacy)
        r.f.s.post(r.f.s.ready("2"))
    source = r.f.items()[0]
    with pytest.raises(BusinessError) as err:
        part(r, "1")
    assert err.value.code == "RETURN_LEGACY_FULL_ONLY"
    rid = uuid4()
    r.f.s.f.world.execute(
        """INSERT INTO med.return_document(tenant_id,id,entity_id,party_id,
        source_posting_id,receivable_id,kind,number,reason,gross_amount,net_amount,created_by)
        SELECT tenant_id,:id,entity_id,customer_id,posting_id,id,'SALES',:number,'旧版申请',
        gross_amount,net_amount,:actor FROM med.receivable_obligation WHERE id=:source""",
        id=rid,
        number=str(rid),
        actor=r.f.maker.principal_id,
        source=UUID(source["id"]),
    )
    r.service.review(r.f.s.f.reviewer, rid, r.review_body(rid), str(uuid4()))
    r.post(rid)
    assert r.detail(rid)["custody"] == []
    assert r.f.items()[0]["returned_amount"] == "40.00"
    with pytest.raises(BusinessError):
        r.create()


def test_full_new_return_of_legacy_source_receives_and_posts(ret, monkeypatch):
    r = ret
    execute = SqlSession.execute

    def legacy(self, sql, params):
        if "INSERT INTO med.posting_line(" in sql:
            params = {**params, "gross": None, "net": None}
        return execute(self, sql, params)

    with monkeypatch.context() as patch:
        patch.setattr(SqlSession, "execute", legacy)
        r.f.s.post(r.f.s.ready("2"))
    rid = r.ready()
    r.post(rid)
    assert r.detail(rid)["custody"][0]["outcome"] == "ACCEPTED"
    with pytest.raises(BusinessError) as err:
        r.create()
    assert err.value.code == "RETURN_EXISTS"
