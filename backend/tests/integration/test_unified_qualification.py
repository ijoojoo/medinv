from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_party_certificates import request as certificate_request
from test_party_certificates import revision
from test_procurement import flow as flow
from test_product_certificates import request as product_certificate_request
from test_product_certificates import revision as product_certificate_revision
from test_product_master import body as master_body
from test_sales import sale as sale

from medinv.modules.catalog.api.admission import AdmissionPreview
from medinv.modules.catalog.api.master import MasterSave
from medinv.modules.catalog.api.qualification import BundleDiscard, RulesSave, ScopeSave
from medinv.modules.catalog.api.schemas import Review
from medinv.modules.catalog.application.admission import AdmissionPolicies
from medinv.modules.catalog.application.certificates import ProductCertificates
from medinv.modules.catalog.application.change_bundle import ChangeBundles
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.modules.catalog.application.master import ProductMaster
from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.modules.catalog.application.qualification_rules import QualificationRules
from medinv.modules.catalog.application.scoped_admission import PartyScopes
from medinv.modules.catalog.application.service import CatalogService
from medinv.modules.inventory.api.schemas import ReserveRequest
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def approve(f, kind="party", target=None, actor=None):
    target = target or UUID(f.party["id"])
    current = CatalogMaintenance(f.world.uow).detail(f.maker, kind, target, 0)["record"]
    return CatalogService(f.world.uow).review(
        actor or f.reviewer,
        kind,
        target,
        Review(
            expected_version=current["version"],
            decision="APPROVE",
            evidence_ref="TEST-UNIFIED",
            reason="一次核对主体全部资料",
        ),
        str(uuid4()),
    )


def config(f, **changes):
    service = QualificationRules(f.world.uow)
    old = service.get(f.maker)
    return service.save(
        f.maker,
        RulesSave(
            expected_version=old["version"],
            payload={**old["payload"], **changes},
            reason="隔离企业软件测试",
        ),
        str(uuid4()),
    )


def setup(f, *, expired=False, channel="PURCHASE"):
    target = UUID(f.party["id"])
    certs = PartyCertificates(f.world.uow)
    uses = ["SUPPLY"] if channel == "PURCHASE" else ["PURCHASE"]
    identity = certs.save(f.maker, target, certificate_request(business_uses=uses), str(uuid4()))
    permit = certs.save(
        f.maker,
        target,
        certificate_request(
            certificate_type="DEVICE_BUSINESS_LICENSE",
            number="TEST-III",
            business_uses=uses,
            validity_mode="FIXED",
            valid_from=f.day - timedelta(days=30),
            valid_until=f.day + timedelta(days=-1 if expired else 30),
        ),
        str(uuid4()),
    )
    config(f, require_own_scope=False)
    scopes = []
    for level in ("I", "III"):
        scopes.append(
            {
                "id": str(uuid4()),
                "category": "DEVICE",
                "name": level + "类 06",
                "device_class": level,
                "directory_version": "2017",
                "codes": ["06"],
                "channels": [channel],
                "certificate_ids": [identity["id"]] + ([permit["id"]] if level == "III" else []),
            }
        )
    service = PartyScopes(f.world.uow)
    service.save(
        f.maker,
        target,
        ScopeSave(
            expected_version=service.get(f.maker, target)["version"],
            payload={"scopes": scopes},
            reason="按证照确认范围",
        ),
        str(uuid4()),
    )
    approve(f)
    f.world.execute(
        "UPDATE med.product SET device_class='III',device_classification_code='06-01-01',"
        "device_classification_version='2017' WHERE id=:p",
        p=UUID(f.product["id"]),
    )
    return target, identity, permit, service


def preview(f):
    return AdmissionPolicies(f.world.uow).preview(
        f.maker,
        f.world.entity,
        "PURCHASE",
        AdmissionPreview(party_id=f.party["id"], sku_ids=[f.sku]),
    )


def test_one_review_activates_scope_and_certificates(flow):
    f = flow
    target, identity, permit, scopes = setup(f)
    assert all(
        c["state"] == "VERIFIED"
        for c in PartyCertificates(f.world.uow).list(f.maker, target)["items"]
    )
    assert not ChangeBundles(f.world.uow).detail(f.maker, "party", target)["has_changes"]
    history = PartyCertificates(f.world.uow).detail(f.maker, target, UUID(identity["id"]))[
        "history"
    ]
    assert history[0]["detail"]["source"] == "parent_review"
    assert history[0]["detail"]["decision"] == "VERIFY"
    assert history[0]["detail"]["certificate_domain"] == "party"
    assert preview(f)["qualified"]
    assert f.order()


def test_category_expiry_does_not_suspend_unit_or_other_class(flow):
    f = flow
    target, *_ = setup(f, expired=True)
    assert not preview(f)["passed"]
    with pytest.raises(BusinessError, match="到期"):
        f.order()
    f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:p", p=UUID(f.product["id"]))
    # The obsolete whole-party deadline must no longer reject covered goods.
    f.world.execute(
        "UPDATE med.party SET valid_from=:start,valid_until=:end WHERE id=:p",
        start=f.day - timedelta(days=20),
        end=f.day - timedelta(days=1),
        p=target,
    )
    assert preview(f)["passed"] and f.order()
    assert f.world.scalar("SELECT state FROM med.party WHERE id=:p", p=target) == "APPROVED"


@pytest.mark.parametrize(
    "code,version,passed",
    [
        ("06-02", "2017", True),
        ("08-01", "2017", False),
        ("060-01", "2017", False),
        ("06-01", "2002", False),
    ],
)
def test_classification_boundary_and_version(flow, code, version, passed):
    f = flow
    setup(f)
    f.world.execute(
        "UPDATE med.product SET device_classification_code=:c,"
        "device_classification_version=:v WHERE id=:p",
        c=code,
        v=version,
        p=UUID(f.product["id"]),
    )
    assert preview(f)["passed"] is passed


def test_exclusion_and_renewal_old_version_stays_active(flow):
    f = flow
    target, _, permit, scopes = setup(f)
    certs = PartyCertificates(f.world.uow)
    live = next(c for c in certs.list(f.maker, target)["items"] if c["id"] == permit["id"])
    changed = certs.save(
        f.maker,
        target,
        revision(live, valid_until=f.day - timedelta(days=1)),
        str(uuid4()),
        UUID(permit["id"]),
    )
    assert changed["has_changes"] and preview(f)["passed"]
    assert f.world.scalar(
        "SELECT valid_until FROM med.party_certificate WHERE id=:c", c=UUID(permit["id"])
    ) == f.day + timedelta(days=30)
    approve(f)
    assert not preview(f)["passed"]
    current = scopes.get(f.maker, target)
    payload = deepcopy(current["payload"])
    payload["scopes"][0]["excluded_codes"] = ["06-01"]
    scopes.save(
        f.maker,
        target,
        ScopeSave(expected_version=current["version"], payload=payload, reason="证照范围排除"),
        str(uuid4()),
    )
    approve(f)
    f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:p", p=UUID(f.product["id"]))
    assert not preview(f)["passed"]


def test_impact_and_enforcement_are_editable_and_audited(flow):
    f = flow
    setup(f, expired=True)
    f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:p", p=UUID(f.product["id"]))
    config(f, impact="PARTY")
    assert not preview(f)["passed"]
    for mode in ("WARN", "OFF"):
        config(f, enforcement=mode)
        result = preview(f)
        assert result["passed"] and not result["qualified"] and result["issues"]
    rules = QualificationRules(f.world.uow).get(f.maker)
    assert len(rules["history"]) == 4
    changed = deepcopy(rules["payload"]["rules"])
    changed[0]["name"] = "企业修改的内置规则"
    changed[0]["enabled"] = False
    config(f, rules=changed)
    assert (
        QualificationRules(f.world.uow).get(f.maker)["payload"]["rules"][0]["name"]
        == changed[0]["name"]
    )


def test_changes_reject_self_review_and_stale_approval(flow):
    f = flow
    target, *_ = setup(f)
    certs = PartyCertificates(f.world.uow)
    certs.save(f.maker, target, certificate_request(number="SECOND-IDENTITY"), str(uuid4()))
    with pytest.raises(BusinessError, match="修订人员"):
        approve(f, actor=f.maker)
    with pytest.raises(BusinessError, match="资料已变化"):
        CatalogService(f.world.uow).review(
            f.reviewer,
            "party",
            target,
            Review(expected_version="2", decision="APPROVE", evidence_ref="TEST", reason="旧窗口"),
            str(uuid4()),
        )
    assert preview(f)["passed"]
    service = ChangeBundles(f.world.uow)
    row = service.detail(f.maker, "party", target)
    service.discard(
        f.maker,
        "party",
        target,
        BundleDiscard(expected_version=row["version"], reason="撤回误录"),
        str(uuid4()),
    )
    assert not service.detail(f.maker, "party", target)["has_changes"]
    assert len(certs.list(f.maker, target)["items"]) == 2


def test_missing_own_scope_and_cross_party_certificate(flow):
    f = flow
    target, _, _, scopes = setup(f)
    config(f, require_own_scope=True)
    assert "本企业" in "；".join(preview(f)["issues"])
    current = scopes.get(f.maker, target)
    current["payload"]["scopes"][0]["certificate_ids"] = [str(uuid4())]
    with pytest.raises(BusinessError, match="不属于"):
        scopes.save(
            f.maker,
            target,
            ScopeSave(
                expected_version=current["version"], payload=current["payload"], reason="不可见证件"
            ),
            str(uuid4()),
        )


def test_product_amendment_keeps_live_values_and_single_approval(flow):
    f = flow
    service = ProductMaster(f.world.uow)
    saved = service.save(f.maker, master_body("daily", name="原商品"), str(uuid4()))
    target = UUID(saved["id"])
    approve(f, "product", target)
    before = service.detail(f.maker, target)
    saved = service.save(
        f.maker,
        MasterSave(
            product_id=target,
            category_node_id=before["category_node_id"],
            form_revision=before["form_revision"],
            expected_version=before["version"],
            values={"name": "修订商品"},
            reason="统一变更",
        ),
        str(uuid4()),
    )
    assert saved["has_changes"]
    assert service.detail(f.maker, target)["values"]["name"] == "修订商品"
    assert f.world.scalar("SELECT name FROM med.product WHERE id=:p", p=target) == "原商品"
    approve(f, "product", target)
    assert f.world.scalar("SELECT name FROM med.product WHERE id=:p", p=target) == "修订商品"
    assert not service.detail(f.maker, target)["has_changes"]


def test_quality_reviewer_applies_product_certificate_renewal_once(flow):
    f = flow
    service = ProductMaster(f.world.uow)
    saved = service.save(
        f.maker,
        master_body(
            "device",
            device_class="I",
            name="统一审核器械",
            regulatory_subject=[{"organization_name": "备案人", "role_code": "DEVICE_FILER"}],
        ),
        str(uuid4()),
    )
    target = UUID(saved["id"])
    certs = ProductCertificates(f.world.uow)
    cert = certs.save(f.maker, target, product_certificate_request(), str(uuid4()))

    def edit(**values):
        detail = service.detail(f.maker, target)
        return service.save(
            f.maker,
            MasterSave(
                product_id=target,
                category_node_id=detail["category_node_id"],
                form_revision=detail["form_revision"],
                expected_version=detail["version"],
                values=values,
                reason="随商品审核",
            ),
            str(uuid4()),
        )

    edit(
        product_certificates=[
            {
                "certificate_id": cert["id"],
                "certificate_version": int(cert["version"]),
                "business_use": True,
            }
        ]
    )
    quality = replace(f.reviewer, permissions=ROLE_PERMISSIONS["QUALITY"])
    approve(f, "product", target, quality)
    live = certs.detail(f.maker, target, UUID(cert["id"]))["record"]
    certs.save(
        f.maker,
        target,
        product_certificate_revision(live, number="续期新编号"),
        str(uuid4()),
        UUID(cert["id"]),
    )
    assert f.world.scalar(
        "SELECT med.product_qualification_current(p) FROM med.product p WHERE id=:p", p=target
    )
    pending = service.detail(f.maker, target)
    assert pending["values"]["product_certificates"][0]["certificate_version"] == 2
    edit(name="新名称", product_certificates=pending["values"]["product_certificates"])
    approve(f, "product", target, quality)
    assert certs.detail(f.maker, target, UUID(cert["id"]))["record"]["state"] == "VERIFIED"
    assert f.world.scalar(
        "SELECT med.product_qualification_current(p) FROM med.product p WHERE id=:p", p=target
    )
    assert service.detail(f.maker, target)["values"]["name"] == "新名称"


def test_failed_approval_rolls_back_all_children_and_live_master(flow):
    f = flow
    target, _, permit, _ = setup(f)
    certs = PartyCertificates(f.world.uow)
    live = certs.detail(f.maker, target, UUID(permit["id"]))["record"]
    certs.save(f.maker, target, revision(live, issuer=""), str(uuid4()), UUID(permit["id"]))
    with pytest.raises(BusinessError, match="签发机关"):
        approve(f)
    assert preview(f)["passed"]
    assert (
        f.world.scalar(
            "SELECT version FROM med.party_certificate WHERE id=:p", p=UUID(permit["id"])
        )
        == 1
    )
    assert ChangeBundles(f.world.uow).detail(f.maker, "party", target)["has_changes"]


def test_replacement_certificate_restores_only_covered_scope(flow):
    f = flow
    target, _, permit, _ = setup(f, expired=True)
    assert not preview(f)["passed"]
    certs = PartyCertificates(f.world.uow)
    current = certs.detail(f.maker, target, UUID(permit["id"]))["record"]
    certs.save(
        f.maker,
        target,
        revision(current, valid_until=f.day + timedelta(days=365)),
        str(uuid4()),
        UUID(permit["id"]),
    )
    assert not preview(f)["passed"]
    approve(f)
    assert preview(f)["passed"] and f.order()
    f.world.execute(
        "UPDATE med.product SET device_classification_code='08' WHERE id=:p",
        p=UUID(f.product["id"]),
    )
    assert not preview(f)["passed"]


def test_new_scope_reservation_replaces_old_unit_deadline_but_checks_category(sale):
    s, f = sale, sale.f
    setup(f, channel="WHOLESALE")
    f.world.execute(
        "UPDATE med.inventory_demand SET eligible_until=now()-interval '1 day' WHERE id=:id",
        id=s.oid,
    )
    f.world.execute(
        "UPDATE med.party SET valid_until=:d WHERE id=:id",
        d=f.day - timedelta(days=1),
        id=UUID(f.party["id"]),
    )
    line = s.detail()["lines"][0]

    def reserve():
        return f.world.service.reserve(
            f.maker,
            ReserveRequest(
                entity_id=f.world.entity,
                warehouse_id=f.world.warehouse,
                source_type="SALES_ORDER",
                source_id=s.oid,
                expected_source_version="1",
                client_operation_id=uuid4(),
                lines=[{"source_line_id": line["id"], "sku_id": f.sku, "base_qty": "1"}],
            ),
            str(uuid4()),
        )

    assert reserve()
    f.world.execute(
        "UPDATE med.product SET device_classification_code='08' WHERE id=:p",
        p=UUID(f.product["id"]),
    )
    with pytest.raises(BusinessError, match="经营范围"):
        reserve()
    config(f, enforcement="OFF")
    f.world.execute("UPDATE med.party SET state='SUSPENDED' WHERE id=:id", id=UUID(f.party["id"]))
    with pytest.raises(BusinessError, match="已停用"):
        reserve()


def test_rule_and_draft_permissions_and_tenant_isolation(flow):
    f = flow
    target, *_ = setup(f)
    viewer = replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    other = replace(f.maker, tenant_id=uuid4())
    rules = QualificationRules(f.world.uow)
    row = rules.get(f.maker)
    with pytest.raises(BusinessError):
        rules.save(
            viewer,
            RulesSave(expected_version=row["version"], payload=row["payload"], reason="越权"),
            str(uuid4()),
        )
    assert rules.get(other)["version"] == "0"
    with pytest.raises(BusinessError):
        PartyScopes(f.world.uow).get(other, target)


@pytest.mark.parametrize("kind", ["party", "product"])
def test_pending_list_and_approval_preserve_later_daily_operations(flow, kind):
    from test_catalog_cards_dictionaries import operation
    from test_catalog_revisions import body, revise, target

    f = flow
    revise(f, kind, request=body(f, kind, name="变更名称"))
    listing = (
        f.catalog.parties(f.maker, f.world.entity, "", 0, state="DRAFT")
        if kind == "party"
        else f.catalog.products(f.maker, "", 0, state="DRAFT")
    )
    assert next(r for r in listing["items"] if r["id"] == str(target(f, kind)))["has_changes"]
    operation(f, kind, values={"business_note": "后续经营备注"})
    approve(f, kind, target(f, kind))
    row = CatalogMaintenance(f.world.uow).detail(f.maker, kind, target(f, kind), 0)["record"]
    assert row["business_note"] == "后续经营备注" and row["name"] == "变更名称"


def test_warn_mode_exposes_document_notice_without_changing_stock(flow):
    f = flow
    setup(f, expired=True)
    config(f, enforcement="WARN")
    order = f.order()
    doc = f.service.detail(f.maker, order)
    assert any("到期" in notice for notice in doc["lines"][0]["qualification_notices"])
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 0


def test_new_party_uses_real_scope_certificates_without_whole_party_dates(flow):
    from medinv.modules.catalog.api.schemas import PartyCreate
    from medinv.platform.database.sql import SqlSession

    f = flow
    f.party = f.catalog.create_party(
        f.maker,
        PartyCreate(
            entity_id=f.world.entity,
            code="NO-LEGACY",
            name="按类目新建单位",
            credit_code="TEST-NO-LEGACY",
            kind="SUPPLIER",
            categories=["DEVICE"],
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError, match="未填写"):
        approve(f)
    target, *_ = setup(f)
    assert preview(f)["passed"]
    with f.world.uow.open(f.maker) as session:
        row = f.service.supplier(SqlSession(session), f.world.entity, target)
        assert row["valid_from"] is None and row["valid_until"] is None


def test_builtin_product_field_requirements_can_be_disabled(flow):
    from test_product_field_rules import create, request, save

    f = flow
    save(f, request(f, approval_number={"required": False}, specification={"required": False}))
    assert create(f, approval_number="", specification="")
