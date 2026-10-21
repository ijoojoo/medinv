import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_api import api as api
from test_party_certificates import request as certificate_request
from test_party_certificates import verification
from test_procurement import flow as flow
from test_procurement import new_stock
from test_sales import sale as sale

from medinv.modules.catalog.api.admission import (
    AdmissionPayload,
    AdmissionPreview,
    AdmissionReview,
    AdmissionSave,
)
from medinv.modules.catalog.application.admission import AdmissionPolicies
from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.modules.inventory.api.schemas import ReserveRequest
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def setup(f, channel="PURCHASE"):
    service = AdmissionPolicies(f.world.uow)
    certs = PartyCertificates(f.world.uow)
    party = UUID(f.party["id"])
    row = certs.save(
        f.maker,
        party,
        certificate_request(business_uses=["SUPPLY" if channel == "PURCHASE" else "PURCHASE"]),
        str(uuid4()),
    )
    cert = certs.review(f.reviewer, party, UUID(row["id"]), verification(row), str(uuid4()))
    f.world.execute(
        """INSERT INTO med.catalog_dictionary(tenant_id,id,key,mode,items,created_by,updated_by)
        VALUES (:t,:id,'device_business_scope','STRICT',CAST(:items AS jsonb),:a,:a)
        ON CONFLICT(tenant_id,key) DO NOTHING""",
        t=f.maker.tenant_id,
        id=uuid4(),
        a=f.maker.principal_id,
        items=json.dumps(
            [
                {"value": "测试范围A", "active": True, "order": 10},
                {"value": "测试范围B", "active": True, "order": 20},
            ]
        ),
    )
    f.world.execute(
        "UPDATE med.product SET device_business_scope='测试范围A' WHERE id=:id",
        id=UUID(f.product["id"]),
    )
    own = {
        "category": "DEVICE",
        "value": "测试范围A",
        "valid_from": str(f.day - timedelta(days=1)),
        "valid_until": str(f.day + timedelta(days=30)),
        "evidence_ref": "TEST-ONLY-SCOPE",
    }
    payload = {
        "enabled": True,
        "own_scopes": [own],
        "party_scopes": [
            {
                **own,
                "party_id": str(party),
                "certificate_id": cert["id"],
                "certificate_version": cert["version"],
            }
        ],
    }
    return service, payload, cert


def save(f, service, payload, channel="PURCHASE"):
    row = service.detail(f.maker, f.world.entity, channel)
    return service.save(
        f.maker,
        f.world.entity,
        channel,
        AdmissionSave(expected_version=row["version"], payload=payload, reason="仅测试规则"),
        str(uuid4()),
    )


def publish(f, service, payload, channel="PURCHASE"):
    row = save(f, service, payload, channel)
    return service.review(
        f.reviewer,
        f.world.entity,
        channel,
        AdmissionReview(expected_version=row["version"], reason="独立核验测试规则"),
        str(uuid4()),
    )


def preview(f, service, source="ACTIVE", channel="PURCHASE"):
    return service.preview(
        f.maker,
        f.world.entity,
        channel,
        AdmissionPreview(party_id=f.party["id"], sku_ids=[f.sku], source=source),
    )


def test_draft_independent_review_and_idempotency(flow):
    f = flow
    service, payload, _ = setup(f)
    body = AdmissionSave(expected_version="0", payload=payload, reason="建立测试规则")
    key = str(uuid4())
    row = service.save(f.maker, f.world.entity, "PURCHASE", body, key)
    assert service.save(f.maker, f.world.entity, "PURCHASE", body, key) == row
    assert not preview(f, service)["enabled"]
    assert preview(f, service, "DRAFT")["passed"]
    review = AdmissionReview(expected_version="1", reason="复核测试")
    with pytest.raises(BusinessError, match="独立复核"):
        service.review(f.maker, f.world.entity, "PURCHASE", review, str(uuid4()))
    active = service.review(f.reviewer, f.world.entity, "PURCHASE", review, str(uuid4()))
    assert active["active_version"] == "2" and not active["has_draft"]
    assert preview(f, service)["enabled"]
    payload["enabled"] = False
    save(f, service, payload)
    assert preview(f, service)["enabled"]  # Draft changes cannot silently bypass the live policy.
    review = AdmissionReview(expected_version="3", reason="复核停用")
    service.review(f.reviewer, f.world.entity, "PURCHASE", review, str(uuid4()))
    assert not preview(f, service)["enabled"]
    assert len(service.detail(f.maker, f.world.entity, "PURCHASE")["history"]) == 4


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_product", "未填写结构化"),
        ("wrong_scope", "本企业缺少"),
        ("dictionary_inactive", "范围字典"),
        ("own_expired", "本企业范围核验"),
        ("party_expired", "往来单位范围核验"),
        ("certificate_changed", "版本已变化"),
        ("certificate_inactive", "尚未核验或已停用"),
        ("certificate_expired", "证照未生效或已过期"),
        ("certificate_use", "业务用途不匹配"),
        ("certificate_unknown", "有效期未明确"),
        ("own_missing", "本企业缺少"),
        ("party_missing", "往来单位缺少"),
    ],
)
def test_dynamic_recheck_before_receiving(flow, change, reason):
    f = flow
    service, payload, cert = setup(f)
    if change in {"own_expired", "party_expired"}:
        grant = payload["own_scopes" if change == "own_expired" else "party_scopes"][0]
        grant["valid_from"] = str(f.day - timedelta(days=3))
        grant["valid_until"] = str(f.day - timedelta(days=1))
    if change == "own_missing":
        payload["own_scopes"][0]["value"] = "测试范围B"
    if change == "party_missing":
        payload["party_scopes"] = []
    publish(f, service, payload)
    if change in {"missing_product", "wrong_scope"}:
        f.world.execute(
            "UPDATE med.product SET device_business_scope=:value WHERE id=:id",
            value="" if change == "missing_product" else "测试范围B",
            id=UUID(f.product["id"]),
        )
    if change == "dictionary_inactive":
        f.world.execute(
            "UPDATE med.catalog_dictionary SET items=jsonb_set(items,'{0,active}','false') "
            "WHERE key='device_business_scope'"
        )
    changes = {
        "certificate_changed": "version=version+1",
        "certificate_inactive": "state='INACTIVE'",
        "certificate_expired": (
            "validity_mode='FIXED',valid_from=current_date-5,valid_until=current_date-1"
        ),
        "certificate_use": "business_uses=ARRAY['PURCHASE']::text[]",
        "certificate_unknown": "validity_mode='NOT_STATED'",
    }
    if change in changes:
        f.world.execute(
            "UPDATE med.party_certificate SET " + changes[change] + " WHERE id=:id",
            id=UUID(cert["id"]),
        )
    result = preview(f, service)
    assert not result["passed"] and any(reason in x for x in result["issues"])
    with pytest.raises(BusinessError) as error:
        f.receive()
    assert error.value.code == "ADMISSION_BLOCKED"
    assert new_stock(f) == 0 and f.world.scalar("SELECT count(*) FROM med.receipt") == 0


def test_post_rechecks_and_replay_keeps_original_success(flow):
    f = flow
    service, payload, cert = setup(f)
    publish(f, service, payload)
    inspected = f.inspect(f.receive()["id"])
    f.world.execute(
        "UPDATE med.party_certificate SET version=version+1 WHERE id=:id", id=UUID(cert["id"])
    )
    with pytest.raises(BusinessError) as error:
        f.post(inspected)
    assert error.value.code == "ADMISSION_BLOCKED" and new_stock(f) == 0
    payload["party_scopes"][0]["certificate_version"] = str(int(cert["version"]) + 1)
    publish(f, service, payload)
    result = f.post(inspected)
    assert new_stock(f) == 10
    snap = f.world.scalar(
        "SELECT product_snapshot FROM med.posting_line WHERE sku_id=:id", id=f.sku
    )
    assert snap["admission"]["policy_version"] == "4"
    f.world.execute(
        "UPDATE med.party_certificate SET version=version+1 WHERE id=:id", id=UUID(cert["id"])
    )
    assert f.post(inspected) == result  # New request key, already posted segment.
    assert new_stock(f) == 10


def test_sales_creation_allocation_and_post_recheck(sale):
    s = sale
    f = s.f
    service, payload, cert = setup(f, "WHOLESALE")
    publish(f, service, payload, "WHOLESALE")
    assert s.order(qty="1")
    shipment = s.ready(qty="2")
    f.world.execute(
        "UPDATE med.party_certificate SET version=version+1 WHERE id=:id", id=UUID(cert["id"])
    )
    for action in [lambda: s.order(qty="1"), lambda: s.allocate(qty="1"), lambda: s.post(shipment)]:
        with pytest.raises(BusinessError) as error:
            action()
        assert error.value.code == "ADMISSION_BLOCKED"
    assert s.scalar("on_hand") == 10
    payload["party_scopes"][0]["certificate_version"] = str(int(cert["version"]) + 1)
    publish(f, service, payload, "WHOLESALE")
    result = s.post(shipment)
    f.world.execute(
        "UPDATE med.party_certificate SET version=version+1 WHERE id=:id", id=UUID(cert["id"])
    )
    assert s.post(shipment) == result and s.scalar("on_hand") == 8


def test_public_reservation_rechecks_scope(sale):
    s = sale
    f = s.f
    service, payload, cert = setup(f, "WHOLESALE")
    publish(f, service, payload, "WHOLESALE")
    f.world.execute(
        "UPDATE med.party_certificate SET version=version+1 WHERE id=:id", id=UUID(cert["id"])
    )
    line = s.detail()["lines"][0]
    req = ReserveRequest(
        entity_id=f.world.entity,
        warehouse_id=f.world.warehouse,
        source_type="SALES_ORDER",
        source_id=s.oid,
        expected_source_version="1",
        client_operation_id=uuid4(),
        lines=[{"source_line_id": line["id"], "sku_id": f.sku, "base_qty": "1"}],
    )
    with pytest.raises(BusinessError) as error:
        f.world.service.reserve(f.maker, req, str(uuid4()))
    assert error.value.code == "ADMISSION_BLOCKED" and s.scalar("reserved") == 0


def test_permissions_scope_concurrent_versions_and_isolation(flow):
    f = flow
    service, payload, _ = setup(f)
    restricted = replace(f.maker, permissions=frozenset({"catalog.read"}))
    body = AdmissionSave(expected_version="0", payload=payload, reason="测试规则")
    with pytest.raises(BusinessError):
        service.save(restricted, f.world.entity, "PURCHASE", body, str(uuid4()))
    foreign = replace(f.maker, tenant_id=uuid4())
    with pytest.raises(BusinessError):
        service.detail(foreign, f.world.entity, "PURCHASE")

    def write():
        try:
            service.save(f.maker, f.world.entity, "PURCHASE", body, str(uuid4()))
            return "OK"
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: write(), range(2))) == ["OK", "VERSION_CONFLICT"]
    assert not service.detail(f.maker, f.world.entity, "WHOLESALE")["active_payload"]["enabled"]
    payload["party_scopes"][0]["party_id"] = str(uuid4())
    with pytest.raises(BusinessError) as error:
        save(f, service, payload)
    assert error.value.code == "NOT_FOUND"


def test_no_empty_enabled_or_duplicate_rules(flow):
    _, payload, _ = setup(flow)
    payload["own_scopes"].append(dict(payload["own_scopes"][0]))
    with pytest.raises(ValidationError):
        AdmissionPayload(**payload)
    with pytest.raises(ValidationError):
        AdmissionPayload(enabled=True)


def test_publication_failure_rolls_back_policy_and_audit(flow):
    f = flow
    service, payload, _ = setup(f)
    save(f, service, payload)
    before = service.detail(f.maker, f.world.entity, "PURCHASE")

    def abort():
        raise BusinessError("ABORT", "提交前授权失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError, match="授权失效"):
            service.review(
                f.reviewer,
                f.world.entity,
                "PURCHASE",
                AdmissionReview(expected_version="1", reason="测试复核"),
                str(uuid4()),
            )
    finally:
        before_business_commit.reset(token)
    assert service.detail(f.maker, f.world.entity, "PURCHASE") == before


def test_publication_serializes_with_purchase_creation(flow):
    f = flow
    service, payload, _ = setup(f)
    payload["party_scopes"] = []
    save(f, service, payload)
    entered, release, attempted = Event(), Event(), Event()

    def hold_commit():
        entered.set()
        assert release.wait(5)

    def review():
        token = before_business_commit.set(hold_commit)
        try:
            return service.review(
                f.reviewer,
                f.world.entity,
                "PURCHASE",
                AdmissionReview(expected_version="1", reason="测试复核"),
                str(uuid4()),
            )
        finally:
            before_business_commit.reset(token)

    def order():
        attempted.set()
        try:
            f.order()
            return "OK"
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        reviewed = pool.submit(review)
        assert entered.wait(5)
        created = pool.submit(order)
        assert attempted.wait(5)
        release.set()
        assert reviewed.result()["active_version"] == "2"
        assert created.result() == "ADMISSION_BLOCKED"
    assert f.world.scalar("SELECT count(*) FROM med.purchase_order") == 1


def test_http_permissions_context_and_validation(api, world):
    client, owner = api
    path = f"/api/v1/catalog/admission/{world.entity}/PURCHASE"
    assert client.get(path).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:p WHERE tenant_id=:t"),
            {"p": list(ROLE_PERMISSIONS["OWNER"]), "t": world.ctx.tenant_id},
        )
    assert client.get(path).json()["data"]["active_version"] == "0"
    body = {"expected_version": "0", "payload": {"enabled": False}, "reason": "接口验证"}
    assert client.post(path, json=body, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.post(path, json=body, headers={"X-Context-Generation": "99"}).status_code == 409
    assert client.post(path, json={**body, "payload": {"enabled": "true"}}).status_code == 422
    response = client.post(path, json=body)
    assert response.status_code == 200 and response.json()["data"]["has_draft"]
    assert client.get(f"/api/v1/catalog/admission/{uuid4()}/PURCHASE").status_code == 404


def test_drug_scope_uses_separate_dictionary_and_inclusive_dates(flow):
    f = flow
    service, payload, _ = setup(f)
    f.world.execute(
        """INSERT INTO med.catalog_dictionary(tenant_id,id,key,mode,items,created_by,updated_by)
        VALUES (:t,:id,'drug_business_scope','STRICT',CAST(:items AS jsonb),:a,:a)""",
        t=f.maker.tenant_id,
        id=uuid4(),
        a=f.maker.principal_id,
        items=json.dumps([{"value": "药品测试范围", "active": True, "order": 10}]),
    )
    f.world.execute(
        """UPDATE med.product SET category='DRUG',drug_sale_class='OTC',
        drug_business_scope='药品测试范围',device_business_scope='' WHERE id=:id""",
        id=UUID(f.product["id"]),
    )
    f.world.execute(
        "UPDATE med.party SET categories=ARRAY['DRUG']::text[] WHERE id=:id", id=UUID(f.party["id"])
    )
    for grant in payload["own_scopes"] + payload["party_scopes"]:
        grant.update(
            category="DRUG", value="药品测试范围", valid_from=str(f.day), valid_until=str(f.day)
        )
    publish(f, service, payload)
    assert preview(f, service)["passed"]
    assert f.order(qty="1")
    f.world.execute(
        "UPDATE med.catalog_dictionary SET items='[]'::jsonb WHERE key='drug_business_scope'"
    )
    assert not preview(f, service)["passed"]
    payload["enabled"] = False
    publish(f, service, payload)
    assert not preview(f, service)["enabled"]
