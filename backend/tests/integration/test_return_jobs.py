from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow
from test_retail import retail as retail
from test_retail_returns import retail_return as retail_return
from test_returns import ret as ret
from test_sales import sale as sale
from test_settlement import finance as finance

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailCashConfirmation
from medinv.modules.returns.api.schemas import (
    CustodyHandover,
    ReturnCreate,
    ReturnReceive,
    ReturnReview,
)
from medinv.modules.returns.application import custody, posting, retail_refunds
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture(params=["PURCHASE", "SALES", "RETAIL"])
def source(request):
    kind = request.param
    if kind == "RETAIL":
        r = request.getfixturevalue("retail_return")
        f, service, pid = r.f, r.service, r.pid
    else:
        r = request.getfixturevalue("ret")
        f, service = r.f.s.f, r.service
        pid = UUID(r.f.items("AP" if kind == "PURCHASE" else "AR")[0]["posting_id"])
    role = {"PURCHASE": "PURCHASER", "SALES": "SALES", "RETAIL": "CASHIER"}[kind]
    body = ReturnCreate(
        client_operation_id=uuid4(),
        source_posting_id=pid,
        reason="按岗位办理原单退一件",
        lines=[
            {
                "source_posting_line_id": service.source_detail(f.maker, pid)["lines"][0]["id"],
                "qty": "1",
            }
        ],
    )

    def actor(job):
        return replace(f.maker, principal_id=uuid4(), permissions=ROLE_PERMISSIONS[job])

    maker, warehouse, quality = actor(role), actor("WAREHOUSE"), actor("QUALITY")
    key = str(uuid4())

    def create():
        return service.create(maker, body, key)

    def detail(rid):
        return service.detail(maker, UUID(str(rid)))

    def checks(rid):
        return [
            {
                "source_posting_line_id": line["id"],
                "qty": line["base_qty"],
                "lot_no": line["lot_no"],
            }
            for line in detail(rid)["lines"]
        ]

    def receive(rid, ctx=None, request_key=None):
        return custody.receive(
            service,
            ctx or warehouse,
            UUID(str(rid)),
            ReturnReceive(
                expected_version="1",
                reason="实际隔离收货核对",
                zone_label="退货隔离位",
                confirmed=True,
                lines=checks(rid),
            ),
            request_key or str(uuid4()),
        )

    def review(rid, ctx=None, decision="APPROVE"):
        return service.review(
            ctx or quality,
            UUID(str(rid)),
            ReturnReview(
                expected_version=detail(rid)["document"]["version"],
                reason="独立质量复核",
                decision=decision,
                lines=checks(rid),
            ),
            str(uuid4()),
        )

    def stock():
        return f.world.scalar("SELECT on_hand FROM med.stock_balance WHERE sku_id=:s", s=f.sku)

    return SimpleNamespace(**locals())


def test_application_kind_and_cancel_are_checked_before_idempotent_replay(source):
    s = source
    for role in ("PURCHASER", "SALES", "CASHIER", "WAREHOUSE", "QUALITY", "FINANCE", "VIEWER"):
        if role == s.role:
            continue
        with pytest.raises(BusinessError) as error:
            s.service.create(s.actor(role), s.body, str(uuid4()))
        assert error.value.status == 403
    assert s.f.world.scalar("SELECT count(*) FROM med.return_document") == 0
    created = s.create()
    assert s.create() == created
    rid = UUID(created["id"])
    removed = replace(s.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    for ctx, status in (
        (removed, 403),
        (replace(s.maker, entity_ids=frozenset([uuid4()])), 404),
        (replace(s.maker, tenant_id=uuid4()), 404),
    ):
        with pytest.raises(BusinessError) as error:
            s.service.create(ctx, s.body, s.key)
        assert error.value.status == status
    transition = Transition(expected_version="1", reason="未交接实物，取消原申请")
    key = str(uuid4())
    for role in ("PURCHASER", "SALES", "CASHIER", "WAREHOUSE"):
        if role == s.role:
            continue
        with pytest.raises(BusinessError) as error:
            s.service.cancel(s.actor(role), rid, transition, key)
        assert error.value.status == 403
    assert s.service.cancel(s.maker, rid, transition, key)["state"] == "CANCELLED"
    with pytest.raises(BusinessError) as error:
        s.service.cancel(removed, rid, transition, key)
    assert error.value.status == 403


def test_job_handoff_preserves_independent_review_and_posts_one_quantity_and_cost(source):
    s = source
    rid = UUID(s.create()["id"])
    before, cost = s.stock(), s.f.world.scalar("SELECT sum(amount) FROM med.cost_balance")
    if s.kind != "PURCHASE":
        with pytest.raises(BusinessError) as error:
            s.receive(rid, s.maker)
        assert error.value.status == 403
        s.receive(rid)
        assert s.stock() == before
        combined_receiver = replace(
            s.warehouse, permissions=s.warehouse.permissions | ROLE_PERMISSIONS["QUALITY"]
        )
        with pytest.raises(BusinessError) as error:
            s.review(rid, combined_receiver)
        assert error.value.code == "MAKER_CHECKER"
    combined_maker = replace(s.maker, permissions=s.maker.permissions | ROLE_PERMISSIONS["QUALITY"])
    with pytest.raises(BusinessError) as error:
        s.review(rid, combined_maker)
    assert error.value.code == "MAKER_CHECKER"
    with pytest.raises(BusinessError) as error:
        s.review(rid, s.maker)
    assert error.value.status == 403
    s.review(rid)
    body = Transition(
        expected_version=s.detail(rid)["document"]["version"], reason="核对原申请实物交接"
    )
    for ctx in (s.maker, s.warehouse, s.actor("FINANCE")):
        with pytest.raises(BusinessError) as error:
            posting.post(s.service, ctx, rid, body, str(uuid4()))
        assert error.value.status == 403
    first = posting.post(s.service, s.quality, rid, body, str(uuid4()))
    assert posting.post(s.service, s.quality, rid, body, str(uuid4())) == first
    direction = -1 if s.kind == "PURCHASE" else 1
    assert s.stock() == before + direction
    assert s.f.world.scalar(
        "SELECT sum(amount) FROM med.cost_balance"
    ) == cost + direction * Decimal("11.504")
    assert s.detail(rid)["document"]["created_by"] == str(s.maker.principal_id)
    assert s.detail(rid)["document"]["reviewed_by"] == str(s.quality.principal_id)
    if s.kind == "RETAIL":
        body = RetailCashConfirmation(
            client_operation_id=uuid4(),
            expected_version=s.detail(rid)["document"]["version"],
            reason="已实际退还20元现金",
            confirmed=True,
        )
        for ctx in (s.maker, s.warehouse, s.quality):
            with pytest.raises(BusinessError) as error:
                retail_refunds.create(s.service, ctx, rid, body, str(uuid4()))
            assert error.value.status == 403
        finance = s.actor("FINANCE")
        key = str(uuid4())
        refund = retail_refunds.create(s.service, finance, rid, body, key)
        assert retail_refunds.create(s.service, finance, rid, body, key) == refund
        assert s.f.world.scalar("SELECT sum(amount) FROM med.retail_return_refund") == 20
        assert s.f.world.scalar("SELECT count(*) FROM med.retail_return_refund") == 1


def test_warehouse_custody_and_rejection_handback_do_not_change_owned_stock(source):
    s = source
    rid = UUID(s.create()["id"])
    before = s.stock()
    if s.kind == "PURCHASE":
        with pytest.raises(BusinessError) as error:
            s.receive(rid)
        assert error.value.code == "INVALID_STATE"
        assert s.detail(rid)["document"]["state"] == "DRAFT"
        assert s.stock() == before
        return
    key = str(uuid4())
    first = s.receive(rid, request_key=key)
    assert s.receive(rid, request_key=key) == first
    for ctx in (
        replace(s.warehouse, entity_ids=frozenset([uuid4()])),
        replace(s.warehouse, tenant_id=uuid4()),
    ):
        with pytest.raises(BusinessError) as error:
            s.receive(rid, ctx, key)
        assert error.value.status == 404
    s.review(rid, decision="REJECT")
    body = CustodyHandover(
        expected_version=s.detail(rid)["document"]["version"],
        reason="拒收实物已全部交还",
        confirmed=True,
    )
    for ctx in (s.maker, s.quality, s.actor("FINANCE")):
        with pytest.raises(BusinessError) as error:
            custody.hand_back(s.service, ctx, rid, body, str(uuid4()))
        assert error.value.status == 403
    key = str(uuid4())
    first = custody.hand_back(s.service, s.warehouse, rid, body, key)
    assert custody.hand_back(s.service, s.warehouse, rid, body, key) == first
    assert s.detail(rid)["document"]["state"] == "CANCELLED"
    assert s.detail(rid)["custody"][0]["outcome"] == "HANDED_BACK"
    assert s.stock() == before
