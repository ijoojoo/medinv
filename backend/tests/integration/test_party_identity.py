from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from medinv.modules.catalog.api.master import OrganizationCreate
from medinv.modules.catalog.api.operations import OperationsUpdate
from medinv.modules.catalog.api.schemas import PartyCreate, PartyRevision, Review
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.modules.catalog.application.master import ProductMaster
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.modules.catalog.application.profile import command_payload
from medinv.modules.catalog.application.service import CatalogService
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    return world, ctx, CatalogService(world.uow), ProductMaster(world.uow)


def data(world, **changes):
    return PartyCreate(
        **{
            "entity_id": world.entity,
            "code": "PARTY-MDM",
            "name": "统一身份机构",
            "credit_code": "MDM-IDENTITY",
            "kind": "SUPPLIER",
            "license_no": "TEST-QUAL",
            "valid_from": date.today(),
            "valid_until": date.today() + timedelta(days=100),
            "categories": ["DEVICE"],
            "organization": {},
            **changes,
        }
    )


def record(world, ctx, target):
    return CatalogMaintenance(world.uow).detail(ctx, "party", UUID(target), 0)["record"]


def test_new_identity_reused_by_product_directory_and_command_retry(setup):
    world, ctx, catalog, master = setup
    key = str(uuid4())
    result = catalog.create_party(ctx, data(world), key)
    assert catalog.create_party(ctx, data(world), key) == result
    party = record(world, ctx, result["id"])
    organizations = master.organizations(ctx, "MDM-IDENTITY")["items"]
    assert len(organizations) == 1
    assert party["organization_id"] == organizations[0]["id"]
    assert party["organization"]["name"] == party["name"]
    assert party["state"] == "DRAFT"
    with pytest.raises(BusinessError, match="已建档"):
        catalog.create_party(
            ctx, data(world, code="ANOTHER", credit_code="mdm-identity"), str(uuid4())
        )
    assert world.scalar("SELECT count(*) FROM med.catalog_organization") == 1


def test_selected_existing_institution_enriches_missing_code_without_duplicate(setup):
    world, ctx, catalog, master = setup
    org = master.create_organization(ctx, OrganizationCreate(name="统一身份机构"), str(uuid4()))
    result = catalog.create_party(ctx, data(world, organization={"id": org["id"]}), str(uuid4()))
    assert record(world, ctx, result["id"])["organization_id"] == org["id"]
    assert world.scalar("SELECT legal_code FROM med.catalog_organization") == "MDM-IDENTITY"
    assert (
        world.scalar(
            "SELECT count(*) FROM med.audit_event WHERE action='master.organization.identified'"
        )
        == 1
    )


def test_identity_conflicts_inactive_and_ambiguous_never_create_side_effects(setup):
    world, ctx, catalog, master = setup
    org = master.create_organization(
        ctx, OrganizationCreate(name="原有机构", legal_code="MDM-IDENTITY"), str(uuid4())
    )
    for body in [data(world), data(world, organization={"id": org["id"]}, credit_code="DIFFERENT")]:
        with pytest.raises(BusinessError) as exc:
            catalog.create_party(ctx, body, str(uuid4()))
        assert exc.value.code == "ORGANIZATION_IDENTITY_CONFLICT"
    world.execute("UPDATE med.catalog_organization SET state='INACTIVE'")
    with pytest.raises(BusinessError) as exc:
        catalog.create_party(ctx, data(world, name="原有机构"), str(uuid4()))
    assert exc.value.code == "ORGANIZATION_INACTIVE"
    for _ in range(2):
        master.create_organization(ctx, OrganizationCreate(name="同名候选"), str(uuid4()))
    with pytest.raises(BusinessError) as exc:
        catalog.create_party(ctx, data(world, name="同名候选", credit_code="NEW"), str(uuid4()))
    assert exc.value.code == "ORGANIZATION_AMBIGUOUS"
    assert world.scalar("SELECT count(*) FROM med.party") == 0


def test_legacy_requests_keep_fingerprints_and_duplicate_records_untouched(setup):
    world, ctx, catalog, master = setup
    payload = data(world).model_dump(mode="json")
    payload.pop("organization")
    old = PartyCreate(**payload)
    assert command_payload(old) == payload
    result = catalog.create_party(ctx, old, str(uuid4()))
    assert record(world, ctx, result["id"])["organization_id"] is None
    with pytest.raises(BusinessError, match="已建档"):
        catalog.create_party(ctx, data(world, code="OTHER"), str(uuid4()))
    assert master.organizations(ctx)["items"] == []


def test_concurrent_creation_and_commit_failure_are_atomic(setup):
    world, ctx, catalog, master = setup

    def submit(i):
        try:
            return catalog.create_party(ctx, data(world, code=f"PAR-{i}"), str(uuid4()))["id"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("PARTY_IDENTITY_EXISTS") == 1
    assert len(master.organizations(ctx)["items"]) == 1

    def abort():
        raise BusinessError("ABORT", "提交失败", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            catalog.create_party(
                ctx,
                data(world, code="ROLLBACK", name="回滚机构", credit_code="ROLLBACK"),
                str(uuid4()),
            )
    finally:
        before_business_commit.reset(token)
    assert master.organizations(ctx, "回滚机构")["items"] == []


def test_scope_and_shared_identity_keep_entity_qualification_separate(setup):
    world, ctx, catalog, master = setup
    created = catalog.create_party(ctx, data(world), str(uuid4()))
    second = uuid4()
    world.execute(
        "INSERT INTO med.legal_entity(tenant_id,id,name,status,created_by,updated_by,credit_code) "
        "VALUES(:t,:id,'第二主体','ACTIVE',:actor,:actor,'TEST-SECOND')",
        t=ctx.tenant_id,
        id=second,
        actor=ctx.principal_id,
    )
    allowed = replace(ctx, entity_ids=ctx.entity_ids | {second})
    other = catalog.create_party(
        allowed, data(world, entity_id=second, kind="CUSTOMER"), str(uuid4())
    )
    assert (
        record(world, allowed, other["id"])["organization_id"]
        == record(world, ctx, created["id"])["organization_id"]
    )
    assert record(world, allowed, other["id"])["state"] == "DRAFT"
    for unauthorized in [
        replace(ctx, entity_ids=frozenset()),
        replace(ctx, permissions=ROLE_PERMISSIONS["VIEWER"]),
        replace(ctx, tenant_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            catalog.create_party(unauthorized, data(world, code="DENIED"), str(uuid4()))
    with pytest.raises(BusinessError):
        record(world, ctx, other["id"])


def test_extend_roles_requires_review_and_keeps_operations_direct(setup):
    world, ctx, catalog, master = setup
    created = catalog.create_party(ctx, data(world), str(uuid4()))
    reviewer = replace(ctx, principal_id=uuid4())
    review = Review(expected_version="1", decision="APPROVE", evidence_ref="TEST", reason="核验")
    catalog.review(reviewer, "party", UUID(created["id"]), review, str(uuid4()))
    base = data(world).model_dump()
    revision = PartyRevision(
        **{k: base[k] for k in ["name", "license_no", "valid_from", "valid_until", "categories"]},
        expected_version="2",
        kind="BOTH",
        reason="增加客户身份",
    )
    service = CatalogMaintenance(world.uow)
    result = service.revise(ctx, "party", UUID(created["id"]), revision, str(uuid4()))
    assert result["state"] == "APPROVED" and result["has_changes"]
    assert catalog.parties(ctx, world.entity, "", 0, "CUSTOMER", "APPROVED")["items"] == []
    with pytest.raises(BusinessError):
        catalog.review(
            ctx,
            "party",
            UUID(created["id"]),
            review.model_copy(update={"expected_version": "3"}),
            str(uuid4()),
        )
    catalog.review(
        reviewer,
        "party",
        UUID(created["id"]),
        review.model_copy(update={"expected_version": "3"}),
        str(uuid4()),
    )
    for kind in ["SUPPLIER", "CUSTOMER"]:
        assert (
            catalog.parties(ctx, world.entity, "", 0, kind, "APPROVED")["items"][0]["id"]
            == created["id"]
        )
    result = CatalogOperations(world.uow).update(
        ctx,
        "parties",
        UUID(created["id"]),
        OperationsUpdate(
            expected_version="4", values={"contact_name": "日常联系人"}, reason="更新联系信息"
        ),
        str(uuid4()),
    )
    assert result["state"] == "APPROVED"
    with pytest.raises(BusinessError) as exc:
        service.revise(
            ctx,
            "party",
            UUID(created["id"]),
            revision.model_copy(update={"kind": "CUSTOMER", "expected_version": "5"}),
            str(uuid4()),
        )
    assert exc.value.code == "PARTY_ROLE_INVALID"
