from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from test_procurement import flow as flow
from test_retail import retail as retail
from test_sales import sale as sale

from medinv.modules.catalog.api.schemas import PartyRevision, ProductRevision, Review
from medinv.modules.catalog.application.maintenance import FIELDS, CatalogMaintenance
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def target(f, kind):
    return UUID((f.product if kind == "product" else f.party)["id"])


def document(f, kind, ctx=None):
    return CatalogMaintenance(f.world.uow).detail(ctx or f.maker, kind, target(f, kind), 0)


def body(f, kind, /, **changes):
    row = document(f, kind)["record"]
    data = {k: row[k] for k in FIELDS[kind]}
    data.update(
        expected_version=row["version"],
        reason="核实资料修订",
        valid_until=f.day + timedelta(days=700),
    )
    data.update(changes)
    return (ProductRevision if kind == "product" else PartyRevision)(**data)


def revise(f, kind, ctx=None, request=None, key=None):
    return CatalogMaintenance(f.world.uow).revise(
        ctx or f.maker, kind, target(f, kind), request or body(f, kind), key or str(uuid4())
    )


def review(f, kind, ctx=None, version=None, decision="APPROVE"):
    return f.catalog.review(
        ctx or f.reviewer,
        kind,
        target(f, kind),
        Review(
            expected_version=version or document(f, kind)["record"]["version"],
            decision=decision,
            evidence_ref="修订复核记录",
            reason="独立核对资料",
        ),
        str(uuid4()),
    )


@pytest.mark.parametrize("kind", ["product", "party"])
def test_revision_blocks_existing_procurement_until_review_and_preserves_posted_facts(flow, kind):
    f = flow
    f.post(f.inspect(f.receive("4")["id"], "4"))
    pending = f.inspect(f.receive("2")["id"], "2")
    snapshot = f.world.scalar("SELECT product_snapshot FROM med.posting_line")
    cost = f.world.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=f.sku)
    result = revise(f, kind, request=body(f, kind, name="资料更正名称"))
    assert result["state"] == "APPROVED" and result["has_changes"]
    row = document(f, kind)["record"]
    assert row["reviewed_by"] is not None and row["reviewed_at"] is not None
    assert row["revision_editors"] == [str(f.maker.principal_id)]
    assert document(f, kind)["active_record"]["name"] != "资料更正名称"
    f.receive("1")
    assert f.world.scalar("SELECT amount FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == cost
    review(f, kind)
    f.post(pending)
    assert f.world.scalar("SELECT qty FROM med.cost_balance WHERE sku_id=:s", s=f.sku) == 6
    with f.world.uow.open(f.maker, readonly=True) as session:
        from sqlalchemy import text

        snapshots = (
            session.execute(text("SELECT product_snapshot FROM med.posting_line")).scalars().all()
        )
    assert snapshot in snapshots
    history = document(f, kind)["history"]
    revision = next(r for r in history if r["action"] == "catalog.change_staged")["detail"]
    assert revision["before"]["name"] != "资料更正名称"
    assert revision["after"]["name"] == "资料更正名称"
    assert revision["version"] == "3"


@pytest.mark.parametrize("kind", ["product", "party"])
def test_all_current_revision_editors_cannot_approve_even_after_suspension(flow, kind):
    f = flow
    a = replace(f.maker, principal_id=uuid4())
    b = replace(f.maker, principal_id=uuid4())
    revise(f, kind, ctx=a)
    revise(f, kind, ctx=b, request=body(f, kind, name="第二人更正"))
    review(f, kind, decision="SUSPEND")
    for ctx in (a, b, f.maker):
        with pytest.raises(BusinessError) as exc:
            review(f, kind, ctx=ctx)
        assert exc.value.code == "SEPARATION_REQUIRED"
    review(f, kind)
    assert document(f, kind)["record"]["revision_editors"] == []
    review(f, kind, decision="SUSPEND")
    revise(f, kind, ctx=a, request=body(f, kind, name="下一轮更正"))
    assert document(f, kind)["record"]["revision_editors"] == [str(a.principal_id)]
    review(f, kind, ctx=b)


@pytest.mark.parametrize("kind", ["product", "party"])
def test_revision_retry_and_no_change_do_not_duplicate_audit_or_reset_review(flow, kind):
    f = flow
    request, key = body(f, kind), str(uuid4())
    first = revise(f, kind, request=request, key=key)
    assert revise(f, kind, request=request, key=key) == first
    with pytest.raises(BusinessError) as exc:
        revise(f, kind, request=request.model_copy(update={"name": "另一个修改"}), key=key)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    review(f, kind)
    with pytest.raises(BusinessError) as exc:
        revise(f, kind)
    assert exc.value.code == "CATALOG_UNCHANGED"
    with pytest.raises(BusinessError) as exc:
        revise(f, kind, request=request)
    assert exc.value.code == "VERSION_CONFLICT"
    assert document(f, kind)["record"]["version"] == "4"
    assert (
        len([x for x in document(f, kind)["history"] if x["action"] == "catalog.change_staged"])
        == 1
    )
    with pytest.raises(DBAPIError):
        f.world.execute(
            "UPDATE med.audit_event SET reason='changed' WHERE resource_id=:id", id=target(f, kind)
        )


@pytest.mark.parametrize("kind", ["product", "party"])
def test_revision_and_approval_serialize_same_document_version(flow, kind):
    f = flow
    request = body(f, kind)

    def run(action):
        try:
            if action == "revise":
                return revise(f, kind, request=request)
            return review(f, kind, version="2")
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["revise", "review"]))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert "VERSION_CONFLICT" in results
    assert document(f, kind)["record"]["version"] == "3"


@pytest.mark.parametrize("kind", ["product", "party"])
def test_revision_scope_and_submit_revocation_roll_back_all_changes(flow, kind):
    f = flow
    request, key = body(f, kind), str(uuid4())
    before = document(f, kind)

    def fail():
        raise BusinessError("REVOKED", "撤权", 403)

    token = before_business_commit.set(fail)
    try:
        with pytest.raises(BusinessError):
            revise(f, kind, request=request, key=key)
    finally:
        before_business_commit.reset(token)
    assert document(f, kind) == before
    revise(f, kind, request=request, key=key)
    for ctx in [
        replace(f.maker, tenant_id=uuid4()),
        replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
    ]:
        with pytest.raises(BusinessError):
            revise(f, kind, ctx=ctx, request=request, key=key)
    if kind == "party":
        ctx = replace(f.maker, entity_ids=frozenset())
        with pytest.raises(BusinessError):
            document(f, kind, ctx)
        with pytest.raises(BusinessError):
            revise(f, kind, ctx=ctx, request=request, key=key)


def test_revision_dto_rejects_identity_changes_and_device_drug_class(flow):
    f = flow
    for kind, changes in [
        ("product", {"base_unit": "箱"}),
        ("product", {"qty_scale": 2}),
        ("product", {"code": "NEW"}),
        ("product", {"category": "DRUG"}),
        ("party", {"entity_id": uuid4()}),
        ("party", {"credit_code": "NEW"}),
        ("party", {"kind": "INVALID"}),
        ("party", {"valid_until": f.day - timedelta(days=10)}),
    ]:
        with pytest.raises(ValidationError):
            body(f, kind, **changes)
    with pytest.raises(BusinessError) as exc:
        revise(f, "product", request=body(f, "product", drug_sale_class="OTC"))
    assert exc.value.code == "DRUG_CLASS_INVALID"


def test_product_revision_blocks_existing_paid_retail_until_independent_review(retail):
    r = retail
    sid = r.create()["id"]
    r.pay(sid)
    old = r.detail(sid)["lines"][0]["product_snapshot"]
    revise(r.f, "product", request=body(r.f, "product", name="修订后的名称"))
    r.complete(sid)
    assert r.detail(sid)["sale"]["state"] == "COMPLETED"
    review(r.f, "product")
    assert r.detail(sid)["lines"][0]["product_snapshot"] == old


def test_customer_scope_revision_blocks_new_wholesale_without_changing_credit(sale):
    s = sale
    before = s.f.world.scalar("SELECT credit_limit FROM med.credit_account")
    revise(s.f, "party", request=body(s.f, "party", categories=["DRUG"]))
    assert s.order("1")
    review(s.f, "party")
    with pytest.raises(BusinessError):
        s.order("1")
    revise(s.f, "party", request=body(s.f, "party", categories=["DEVICE", "DRUG"]))
    review(s.f, "party")
    assert s.order("1")
    assert s.f.world.scalar("SELECT credit_limit FROM med.credit_account") == before
