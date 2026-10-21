from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from qualification_helpers import review_parent
from sqlalchemy import text
from test_catalog_revisions import body, document, revise
from test_party_identity import data
from test_procurement import flow as flow

from medinv.modules.catalog.api.operations import OperationsUpdate
from medinv.modules.catalog.api.party_certificates import (
    PARTY_CERTIFICATE_FIELDS,
    PartyCertificateCreate,
    PartyCertificateReview,
    PartyCertificateRevision,
)
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.modules.catalog.application.service import CatalogService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def request(**changes):
    return PartyCertificateCreate(
        **{
            "certificate_type": "BUSINESS_LICENSE",
            "number": "TEST-BUSINESS",
            "holder_name": "软件测试单位",
            "issuer": "软件测试签发单位",
            "business_uses": ["SUPPLY"],
            "validity_mode": "LONG_TERM",
            "evidence_ref": "TEST-ORIGINAL",
            "reason": "按原件建档",
            **changes,
        }
    )


def revision(row, **changes):
    return PartyCertificateRevision(
        **{
            **{k: row[k] for k in PARTY_CERTIFICATE_FIELDS},
            "expected_version": row["version"],
            "reason": "核实原件后修订",
            **changes,
        }
    )


def verification(row, **changes):
    return PartyCertificateReview(
        **{
            "expected_version": row["version"],
            "decision": "VERIFY",
            "evidence_ref": "TEST-REVIEW",
            "reason": "独立核对原件",
            **changes,
        }
    )


def setup(f):
    return PartyCertificates(f.world.uow), UUID(f.party["id"])


def test_multiple_certificates_idempotency_and_existing_trade_gate_remain_independent(flow):
    f = flow
    service, party = setup(f)
    before = document(f, "party")["record"]
    payload, key = request(), str(uuid4())
    first = service.save(f.maker, party, payload, key)
    assert service.save(f.maker, party, payload, key) == first
    with pytest.raises(BusinessError):
        service.save(f.maker, party, request(number="DIFFERENT"), key)
    service.save(
        f.maker,
        party,
        request(
            certificate_type="DEVICE_BUSINESS_FILING",
            number="TEST-FILING",
            validity_mode="NOT_STATED",
            scope_text="原件范围" * 200,
        ),
        str(uuid4()),
    )
    listing = service.list(f.maker, party)
    assert len(listing["items"]) == 2
    assert listing["legacy"]["number"] == before["license_no"]
    assert listing["legacy"]["type"] == "LEGACY_UNCLASSIFIED"
    assert listing["business_uses"] == {"SUPPLY": "作为供应商供货"}
    assert document(f, "party")["record"]["state"] == "APPROVED"
    assert document(f, "party")["record"]["name"] == before["name"]
    f.order()


def test_authorized_contacts_independent_review_and_daily_changes_keep_separate_versions(flow):
    f = flow
    service, party = setup(f)
    row = service.save(
        f.maker,
        party,
        request(
            certificate_type="AUTHORIZATION",
            authorized_name="授权张女士",
            authorized_phone="010-12345678",
            authorization_duties="仅业务洽谈及供货",
            validity_mode="FIXED",
            valid_from=f.day,
            valid_until=f.day + timedelta(days=90),
        ),
        str(uuid4()),
    )
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="不能审核"):
        review_parent(service, f.maker, party, target, verification(row), str(uuid4()))
    key = str(uuid4())
    verified = review_parent(service, f.reviewer, party, target, verification(row), key)
    assert service.detail(f.reviewer, party, target)["record"] == verified
    parent = document(f, "party")["record"]
    CatalogOperations(f.world.uow).update(
        f.maker,
        "parties",
        party,
        OperationsUpdate(
            expected_version=parent["version"],
            values={"contact_name": "日常李先生"},
            reason="日常联系维护",
        ),
        str(uuid4()),
    )
    assert service.detail(f.maker, party, target)["record"] == verified
    assert document(f, "party")["record"]["state"] == "APPROVED"
    assert document(f, "party")["record"]["contact_name"] == "日常李先生"
    editor = replace(f.maker, principal_id=uuid4())
    edited = service.save(
        editor, party, revision(verified, authorization_duties="仅供货"), str(uuid4()), target
    )
    assert edited["state"] == "DRAFT" and edited["reviewed_by"] is None
    second_editor = replace(f.maker, principal_id=uuid4())
    edited = service.save(
        second_editor, party, revision(edited, note="补充原件说明"), str(uuid4()), target
    )
    for actor in [f.maker, editor, second_editor]:
        with pytest.raises(BusinessError, match="不能审核"):
            review_parent(service, actor, party, target, verification(edited), str(uuid4()))
    review_parent(service, f.reviewer, party, target, verification(edited), str(uuid4()))
    events = service.detail(f.maker, party, target)["history"]
    assert len(events) == 5
    assert events[2]["detail"]["before"]["authorization_duties"] == "仅业务洽谈及供货"
    assert events[2]["detail"]["after"]["authorization_duties"] == "仅供货"
    assert events[0]["detail"]["source"] == "parent_review"


@pytest.mark.parametrize(
    "missing",
    [
        "holder_name",
        "issuer",
        "evidence_ref",
        "validity_mode",
        "authorized_name",
        "authorized_phone",
        "authorization_duties",
    ],
)
def test_incomplete_evidence_or_authorization_cannot_be_verified(flow, missing):
    service, party = setup(flow)
    values = {
        "certificate_type": "AUTHORIZATION",
        "authorized_name": "授权人",
        "authorized_phone": "010-12345678",
        "authorization_duties": "订货",
        missing: "UNCONFIRMED" if missing == "validity_mode" else "",
    }
    row = service.save(flow.maker, party, request(**values), str(uuid4()))
    with pytest.raises(BusinessError) as exc:
        review_parent(
            service, flow.reviewer, party, UUID(row["id"]), verification(row), str(uuid4())
        )
    assert exc.value.code == "CERTIFICATE_NOT_READY"
    assert service.detail(flow.maker, party, UUID(row["id"]))["record"]["state"] == "DRAFT"


@pytest.mark.parametrize(
    "start,end,status", [(0, 0, "IN_PERIOD"), (-2, -1, "EXPIRED"), (1, 2, "UPCOMING")]
)
def test_record_verification_does_not_hide_current_validity_or_approve_party(
    flow, start, end, status
):
    f = flow
    service, party = setup(f)
    row = service.save(
        f.maker,
        party,
        request(
            validity_mode="FIXED",
            valid_from=f.day + timedelta(days=start),
            valid_until=f.day + timedelta(days=end),
        ),
        str(uuid4()),
    )
    # One parent review confirms all evidence, while validity remains a separate result.
    revise(f, "party", request=body(f, "party", name="修订后待审核单位"))
    reviewed = review_parent(
        service, f.reviewer, party, UUID(row["id"]), verification(row), str(uuid4())
    )
    assert reviewed["state"] == "VERIFIED" and reviewed["validity"] == status
    assert document(f, "party")["record"]["state"] == "APPROVED"


def test_business_use_revalidated_after_party_role_change(flow):
    f = flow
    service, party = setup(f)
    with pytest.raises(BusinessError, match="供销身份"):
        service.save(f.maker, party, request(business_uses=["PURCHASE"]), str(uuid4()))
    revise(f, "party", request=body(f, "party", kind="BOTH"))
    row = service.save(f.maker, party, request(business_uses=["SUPPLY", "PURCHASE"]), str(uuid4()))
    with pytest.raises(BusinessError, match="不能在此缩减"):
        revise(f, "party", request=body(f, "party", kind="SUPPLIER"))
    # Defend against incompatible legacy/imported records as well; the current
    # maintenance command intentionally does not allow shrinking established roles.
    f.world.execute(
        "UPDATE med.catalog_change_bundle SET payload=jsonb_set("
        "payload,'{record,kind}','\"SUPPLIER\"'::jsonb) WHERE target_id=:id",
        id=party,
    )
    assert service.list(f.maker, party)["items"][0]["compatibility"] == "MISMATCH"
    with pytest.raises(BusinessError, match="供销身份"):
        review_parent(service, f.reviewer, party, UUID(row["id"]), verification(row), str(uuid4()))
    fixed = service.save(
        f.maker, party, revision(row, business_uses=["SUPPLY"]), str(uuid4()), UUID(row["id"])
    )
    assert fixed["compatibility"] == "MATCH"


def test_shared_organization_has_separate_entity_certificates_and_read_write_permissions(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    catalog, service = CatalogService(world.uow), PartyCertificates(world.uow)
    first = catalog.create_party(ctx, data(world), str(uuid4()))
    second_entity = uuid4()
    world.execute(
        "INSERT INTO med.legal_entity(tenant_id,id,name,status,created_by,updated_by,credit_code) "
        "VALUES(:t,:id,'第二主体','ACTIVE',:a,:a,'TEST-OTHER')",
        t=ctx.tenant_id,
        id=second_entity,
        a=ctx.principal_id,
    )
    allowed = replace(ctx, entity_ids=ctx.entity_ids | {second_entity})
    second = catalog.create_party(allowed, data(world, entity_id=second_entity), str(uuid4()))
    parent = CatalogMaintenance(world.uow)
    assert (
        parent.detail(ctx, "party", UUID(first["id"]), 0)["record"]["organization_id"]
        == (parent.detail(allowed, "party", UUID(second["id"]), 0)["record"]["organization_id"])
    )
    row = service.save(ctx, UUID(first["id"]), request(), str(uuid4()))
    target = UUID(row["id"])
    assert service.list(allowed, UUID(second["id"]))["items"] == []
    other = service.save(allowed, UUID(second["id"]), request(), str(uuid4()))
    assert other["id"] != row["id"]
    viewer = replace(ctx, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert len(service.list(viewer, UUID(first["id"]))["items"]) == 1
    denied = [
        lambda: service.list(ctx, UUID(second["id"])),
        lambda: service.detail(ctx, UUID(second["id"]), target),
        lambda: service.detail(allowed, UUID(second["id"]), target),
        lambda: service.save(viewer, UUID(first["id"]), request(number="NO"), str(uuid4())),
        lambda: review_parent(
            service, viewer, UUID(first["id"]), target, verification(row), str(uuid4())
        ),
        lambda: service.save(
            ctx, UUID(second["id"]), revision(other, note="越界"), str(uuid4()), UUID(other["id"])
        ),
        lambda: service.list(replace(ctx, tenant_id=uuid4()), UUID(first["id"])),
    ]
    for operation in denied:
        with pytest.raises(BusinessError):
            operation()
    with world.uow.open(replace(ctx, tenant_id=uuid4()), readonly=True) as session:
        assert session.execute(text("SELECT count(*) FROM med.party_certificate")).scalar_one() == 0


def test_concurrent_duplicates_and_revisions_have_single_winner(flow):
    service, party = setup(flow)

    def create(number):
        try:
            return service.save(flow.maker, party, request(number=number), str(uuid4()))["state"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, ["Case-Same", "case-same"])) == [
            "CERTIFICATE_EXISTS",
            "DRAFT",
        ]
    row = service.list(flow.maker, party)["items"][0]

    def change(note):
        try:
            return service.save(
                flow.maker, party, revision(row, note=note), str(uuid4()), UUID(row["id"])
            )["state"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, ["甲", "乙"])) == ["DRAFT", "VERSION_CONFLICT"]
    assert len(service.detail(flow.maker, party, UUID(row["id"]))["history"]) == 2


@pytest.mark.parametrize("action", ["create", "revise", "review"])
def test_commit_failure_rolls_back_record_and_audit(flow, action):
    f = flow
    service, party = setup(f)
    row = service.save(f.maker, party, request(), str(uuid4()))
    target = UUID(row["id"])
    before = service.detail(f.maker, party, target)

    def abort():
        raise BusinessError("ABORT", "提交前授权失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError, match="授权失效"):
            if action == "create":
                service.save(f.maker, party, request(number="ROLLED-BACK"), str(uuid4()))
            elif action == "revise":
                service.save(f.maker, party, revision(row, note="回滚"), str(uuid4()), target)
            else:
                review_parent(service, f.reviewer, party, target, verification(row), str(uuid4()))
    finally:
        before_business_commit.reset(token)
    assert service.detail(f.maker, party, target) == before
    assert len(service.list(f.maker, party)["items"]) == 1


def test_inactivation_history_and_reactivation_require_new_revision(flow):
    f = flow
    service, party = setup(f)
    row = service.save(f.maker, party, request(), str(uuid4()))
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="没有变化"):
        service.save(f.maker, party, revision(row), str(uuid4()), target)
    row = review_parent(service, f.reviewer, party, target, verification(row), str(uuid4()))
    row = review_parent(
        service, f.reviewer, party, target, verification(row, decision="INACTIVATE"), str(uuid4())
    )
    with pytest.raises(BusinessError, match="随主体"):
        service.review(f.reviewer, party, target, verification(row), str(uuid4()))
    revised = service.save(f.maker, party, revision(row, note="重新提供原件"), str(uuid4()), target)
    with pytest.raises(BusinessError, match="已变化"):
        service.save(f.reviewer, party, revision(row, note="旧版本"), str(uuid4()), target)
    assert revised["state"] == "DRAFT"


@pytest.mark.parametrize(
    "changes",
    [
        {"business_uses": []},
        {"business_uses": ["SUPPLY", "SUPPLY"]},
        {"business_uses": ["ALL"]},
        {"certificate_type": "DRUG_APPROVAL"},
        {"number": " "},
        {"validity_mode": "FIXED"},
        {"valid_from": "2026-01-01"},
        {"validity_mode": "FIXED", "valid_from": "2027-01-01", "valid_until": "2026-01-01"},
        {
            "validity_mode": "FIXED",
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
            "issued_on": "2027-01-01",
        },
        {"authorized_name": "非授权书不填写联系人"},
        {"authorized_phone": "not-a-phone"},
        {"state": "VERIFIED"},
    ],
)
def test_invalid_certificate_requests_are_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)
