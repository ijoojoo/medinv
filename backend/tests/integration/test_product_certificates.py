from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_catalog_revisions import body, document, revise
from test_procurement import flow as flow

from medinv.modules.catalog.api.certificates import (
    CERTIFICATE_FIELDS,
    CertificateCreate,
    CertificateReview,
    CertificateRevision,
)
from medinv.modules.catalog.api.operations import OperationsUpdate
from medinv.modules.catalog.application.certificates import ProductCertificates
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def setup(f, device_class="I"):
    f.world.execute(
        "UPDATE med.product SET device_class=:c WHERE id=:id", c=device_class, id=f.product["id"]
    )
    return ProductCertificates(f.world.uow), UUID(f.product["id"])


def request(**changes):
    return CertificateCreate(
        **{
            "certificate_type": "DEVICE_FILING",
            "number": "TEST-FILING",
            "holder_name": "测试备案人",
            "issuer": "测试备案机关",
            "validity_mode": "NOT_STATED",
            "evidence_ref": "TEST-ORIGINAL",
            "reason": "按原件建档",
            **changes,
        }
    )


def verification(row, **changes):
    return CertificateReview(
        **{
            "expected_version": row["version"],
            "decision": "VERIFY",
            "evidence_ref": "TEST-VERIFY",
            "reason": "核验原件",
            **changes,
        }
    )


def revision(row, **changes):
    return CertificateRevision(
        **{
            **{k: row[k] for k in CERTIFICATE_FIELDS},
            "expected_version": row["version"],
            "reason": "按原件修订",
            **changes,
        }
    )


def test_multiple_certificates_preserve_legacy_product_and_procurement(flow):
    f = flow
    service, product = setup(f)
    before = document(f, "product")["record"]
    legacy = service.list(f.maker, product)["legacy"]
    assert legacy["type"] == "LEGACY_UNCLASSIFIED"
    assert legacy["number"] == before["approval_number"]
    assert "issued_on" not in legacy
    data, key = request(), str(uuid4())
    first = service.save(f.maker, product, data, key)
    assert service.save(f.maker, product, data, key) == first
    service.save(f.maker, product, request(number="TEST-SECOND"), str(uuid4()))
    assert len(service.list(f.maker, product)["items"]) == 2
    assert document(f, "product")["record"] == before
    f.order()  # supplemental certificate drafts do not silently replace the existing trade gate
    detail = service.detail(f.maker, product, UUID(first["id"]))
    assert detail["history"][0]["detail"]["after"]["number"] == "TEST-FILING"
    assert not detail["has_more"]


def test_independent_review_revision_history_and_operations_keep_their_own_versions(flow):
    f = flow
    service, product = setup(f)
    row = service.save(f.maker, product, request(), str(uuid4()))
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="不能核验"):
        service.review(f.maker, product, target, verification(row), str(uuid4()))
    verified = service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    assert verified["state"] == "VERIFIED" and verified["validity"] == "NOT_STATED"
    parent = document(f, "product")["record"]
    CatalogOperations(f.world.uow).update(
        f.maker,
        "products",
        product,
        OperationsUpdate(
            expected_version=parent["version"],
            values={"business_note": "运营修改"},
            reason="正常经营维护",
        ),
        str(uuid4()),
    )
    assert service.detail(f.maker, product, target)["record"]["version"] == verified["version"]
    assert document(f, "product")["record"]["approval_state"] == "APPROVED"
    editor = replace(f.maker, principal_id=uuid4())
    edited = service.save(
        editor, product, revision(verified, covered_models="新增规格"), str(uuid4()), target
    )
    assert edited["state"] == "DRAFT" and edited["reviewed_by"] is None
    with pytest.raises(BusinessError, match="不能核验"):
        service.review(editor, product, target, verification(edited), str(uuid4()))
    service.review(f.reviewer, product, target, verification(edited), str(uuid4()))
    events = service.detail(f.maker, product, target)["history"]
    assert len(events) == 4
    assert events[1]["detail"]["before"]["state"] == "VERIFIED"
    assert events[1]["detail"]["after"]["covered_models"] == "新增规格"


def test_category_and_device_class_revalidated_at_review_and_reported_after_change(flow):
    f = flow
    service, product = setup(f, "UNKNOWN")
    row = service.save(f.maker, product, request(), str(uuid4()))
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="先核定"):
        service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    with pytest.raises(BusinessError, match="类型与"):
        service.save(f.maker, product, request(certificate_type="DRUG_APPROVAL"), str(uuid4()))
    revise(f, "product", request=body(f, "product", device_class="I"))
    service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    revise(f, "product", request=body(f, "product", device_class="II", approval_number="NEW-REG"))
    assert service.list(f.maker, product)["items"][0]["compatibility"] == "MISMATCH"
    with pytest.raises(BusinessError, match="类型与"):
        service.save(f.maker, product, request(number="ANOTHER"), str(uuid4()))


def test_unconfirmed_period_and_registration_missing_dates_cannot_be_verified(flow):
    f = flow
    service, product = setup(f, "II")
    row = service.save(
        f.maker, product, request(certificate_type="DEVICE_REGISTRATION"), str(uuid4())
    )
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="有效期"):
        service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    revised = service.save(
        f.maker,
        product,
        revision(
            row,
            validity_mode="FIXED",
            valid_from=f.day - timedelta(days=40),
            valid_until=f.day - timedelta(days=1),
        ),
        str(uuid4()),
        target,
    )
    verified = service.review(f.reviewer, product, target, verification(revised), str(uuid4()))
    # Verification of historic evidence never means currently in force.
    assert verified["state"] == "VERIFIED" and verified["validity"] == "EXPIRED"
    row = service.save(
        f.maker,
        product,
        request(
            certificate_type="DEVICE_REGISTRATION",
            number="INCOMPLETE",
            validity_mode="UNCONFIRMED",
            issuer="",
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError, match="补齐"):
        service.review(f.reviewer, product, UUID(row["id"]), verification(row), str(uuid4()))


def test_conflict_duplicate_and_cross_product_tenant_permissions(flow):
    f = flow
    service, product = setup(f)
    row = service.save(f.maker, product, request(), str(uuid4()))
    target = UUID(row["id"])
    with pytest.raises(BusinessError, match="已有"):
        service.save(f.maker, product, request(), str(uuid4()))
    with pytest.raises(BusinessError, match="没有变化"):
        service.save(f.maker, product, revision(row), str(uuid4()), target)
    service.save(f.maker, product, revision(row, note="更新"), str(uuid4()), target)
    with pytest.raises(BusinessError, match="已变化"):
        service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    other_product = f.world.scalar("SELECT product_id FROM med.sku WHERE id=:id", id=f.world.sku)
    for action in [
        lambda: service.list(replace(f.maker, tenant_id=uuid4()), product),
        lambda: service.detail(f.maker, other_product, target),
        lambda: service.save(
            f.maker, other_product, revision(row, note="越界"), str(uuid4()), target
        ),
        lambda: service.save(
            replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]),
            product,
            request(number="NO"),
            str(uuid4()),
        ),
        lambda: service.review(
            replace(f.maker, permissions=ROLE_PERMISSIONS["PURCHASER"]),
            product,
            target,
            verification(row),
            str(uuid4()),
        ),
    ]:
        with pytest.raises(BusinessError):
            action()


def test_competing_revisions_only_one_wins_and_failed_commit_has_no_audit(flow):
    f = flow
    service, product = setup(f)
    row = service.save(f.maker, product, request(), str(uuid4()))
    target = UUID(row["id"])

    def update(note):
        try:
            return service.save(f.maker, product, revision(row, note=note), str(uuid4()), target)[
                "state"
            ]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["甲", "乙"]))
    assert sorted(results) == ["DRAFT", "VERSION_CONFLICT"]
    before = service.detail(f.maker, product, target)

    def abort():
        raise BusinessError("ABORT", "提交前授权失败", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError, match="授权失败"):
            service.save(
                f.maker, product, revision(before["record"], note="不能提交"), str(uuid4()), target
            )
    finally:
        before_business_commit.reset(token)
    assert service.detail(f.maker, product, target) == before


def test_inactive_needs_revision_before_reverification(flow):
    f = flow
    service, product = setup(f)
    row = service.save(f.maker, product, request(), str(uuid4()))
    target = UUID(row["id"])
    row = service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    row = service.review(
        f.reviewer, product, target, verification(row, decision="INACTIVATE"), str(uuid4())
    )
    assert row["state"] == "INACTIVE"
    with pytest.raises(BusinessError, match="仅待核验"):
        service.review(f.reviewer, product, target, verification(row), str(uuid4()))
    row = service.save(
        f.maker, product, revision(row, note="补充重新启用依据"), str(uuid4()), target
    )
    assert row["state"] == "DRAFT"


def test_drug_certificate_types_and_row_security_are_independent(flow):
    f = flow
    service, product = setup(f)
    f.world.execute(
        "UPDATE med.product SET category='DRUG',device_class='UNKNOWN' WHERE id=:id", id=product
    )
    assert service.list(f.maker, product)["types"] == {"DRUG_APPROVAL": "药品批准证明"}
    with pytest.raises(BusinessError, match="类型与"):
        service.save(f.maker, product, request(), str(uuid4()))
    row = service.save(
        f.maker,
        product,
        request(
            certificate_type="DRUG_APPROVAL",
            validity_mode="FIXED",
            valid_from=f.day,
            valid_until=f.day + timedelta(days=365),
            covered_models="型" * 500,
        ),
        str(uuid4()),
    )
    verified = service.review(f.reviewer, product, UUID(row["id"]), verification(row), str(uuid4()))
    assert verified["validity"] == "IN_PERIOD"
    from sqlalchemy import text

    with f.world.uow.open(replace(f.maker, tenant_id=uuid4()), readonly=True) as session:
        assert (
            session.execute(text("SELECT count(*) FROM med.product_certificate")).scalar_one() == 0
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"validity_mode": "FIXED"},
        {"valid_from": "2026-01-01"},
        {"validity_mode": "FIXED", "valid_from": "2027-01-01", "valid_until": "2026-01-01"},
        {
            "validity_mode": "FIXED",
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
            "issued_on": "2027-01-01",
        },
        {"number": " "},
        {"certificate_type": "LEGACY_UNCLASSIFIED"},
        {"state": "VERIFIED"},
    ],
)
def test_invalid_certificate_input_is_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)
