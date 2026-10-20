from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_catalog_revisions import body, document, review, revise
from test_packaging import approved
from test_procurement import flow as flow

from medinv.modules.catalog.api.codes import CatalogCodeCreate, CatalogCodeState
from medinv.modules.catalog.api.party_profile import PARTY_PROFILE_FIELDS
from medinv.modules.catalog.api.schemas import PartyCreate, PartyRevision
from medinv.modules.catalog.application.codes import CatalogCodes
from medinv.modules.catalog.application.profile import command_payload
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def code(**changes):
    return CatalogCodeCreate(
        scheme="BARCODE",
        external_code="00123-AbC",
        evidence_ref="TEST-LABEL",
        reason="测试核对标签",
    ).model_copy(update=changes)


def add(f, body=None, kind="skus", target=None, key=None, ctx=None):
    return CatalogCodes(f.world.uow).create(
        ctx or f.maker,
        kind,
        target or (f.sku if kind == "skus" else UUID(f.party["id"])),
        body or code(),
        key or str(uuid4()),
    )


def state(f, row, value="INACTIVE"):
    return CatalogCodes(f.world.uow).change_state(
        f.maker,
        "skus",
        f.sku,
        UUID(row["id"]),
        CatalogCodeState(expected_version=row["version"], state=value, reason="核验调整"),
        str(uuid4()),
    )


def test_party_profile_roundtrip_legacy_revision_review_and_search(flow):
    f = flow
    fields = dict(
        short_name="往来简称",
        search_alias="GY-QX",
        organization_type="WHOLESALER",
        contact_name="测试联系人",
        contact_phone="010-12345678 转 9",
        registered_address="测试注册地址",
        business_address="测试经营地址",
        delivery_address="测试收发货地址",
        legal_representative="测试负责人",
        qualification_archive_ref="ARCHIVE-1",
        authorization_archive_ref="AUTH-1",
        quality_agreement_ref="QA-1",
        business_scope_text="依实际许可范围核验",
    )
    request = PartyCreate(
        entity_id=f.world.entity,
        code="PROFILE",
        name="档案单位",
        credit_code="TEST-PROFILE",
        kind="BOTH",
        license_no="TEST-ONLY",
        valid_from=f.day,
        valid_until=f.day,
        categories=["DEVICE"],
        **fields,
    )
    key = str(uuid4())
    created = f.catalog.create_party(f.maker, request, key)
    assert f.catalog.create_party(f.maker, request, key) == created
    for term in ["gy-qx", "往来简称", "测试联系人", "12345678", "TEST-PROFILE"]:
        for role in ["SUPPLIER", "CUSTOMER", "BOTH"]:
            result = f.catalog.parties(f.maker, f.world.entity, term, 0, role, "DRAFT")
            assert result["items"][0]["id"] == created["id"]
            assert all(result["items"][0][k] == v for k, v in fields.items())
    revise(f, "party", request=body(f, "party", **fields))
    with pytest.raises(BusinessError, match="建档人"):
        review(f, "party", ctx=f.maker)
    review(f, "party")
    legacy = {
        k: v
        for k, v in body(f, "party", name="旧客户端修订").model_dump().items()
        if k not in PARTY_PROFILE_FIELDS
    }
    revision = PartyRevision(**legacy)
    assert not (set(PARTY_PROFILE_FIELDS) & command_payload(revision).keys())
    revise(f, "party", request=revision)
    row = document(f, "party")["record"]
    assert all(row[k] == v for k, v in fields.items())
    revise(f, "party", request=body(f, "party", contact_phone=""))
    detail = document(f, "party")
    assert detail["record"]["contact_phone"] == ""
    assert detail["history"][0]["detail"]["before"]["contact_phone"] == fields["contact_phone"]


def test_codes_retry_search_scope_and_exact_strings(flow):
    f = flow
    key = str(uuid4())
    created = add(f, key=key)
    assert add(f, key=key) == created
    svc = CatalogCodes(f.world.uow)
    result = svc.list(f.maker, "skus", f.sku, 0)
    assert result["items"][0]["external_code"] == "00123-AbC"
    assert len(result["history"]) == 1
    assert f.catalog.products(f.maker, "00123-abc", 0, "DEVICE", "APPROVED")["items"][0][
        "sku_id"
    ] == str(f.sku)
    assert f.catalog.products(f.maker, "00123", 0, "DRUG")["items"] == []
    with pytest.raises(BusinessError) as exc:
        add(f, key=key, ctx=replace(f.maker, tenant_id=uuid4()))
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(BusinessError) as exc:
        add(f, key=key, ctx=replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"]))
    assert exc.value.status == 403
    other = add(f, body=code(external_code="00123-abc"))
    assert other["id"] != created["id"]
    with pytest.raises(BusinessError) as exc:
        add(f)
    assert exc.value.code == "EXTERNAL_CODE_EXISTS"


def test_codes_state_version_collision_and_history_immutable(flow):
    f = flow
    created = add(f)
    disabled = state(f, created)
    assert f.catalog.products(f.maker, "00123-AbC", 0)["items"] == []
    with pytest.raises(BusinessError) as exc:
        state(f, created)
    assert exc.value.code == "VERSION_CONFLICT"
    replacement = add(f)
    with pytest.raises(BusinessError) as exc:
        state(f, disabled, "ACTIVE")
    assert exc.value.code == "EXTERNAL_CODE_EXISTS"
    state(f, replacement)
    assert state(f, disabled, "ACTIVE")["version"] == "3"
    for sql in [
        "UPDATE med.catalog_code SET external_code='changed' WHERE id=:id",
        "DELETE FROM med.catalog_code WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            f.world.execute(sql, id=UUID(created["id"]))
    rows = CatalogCodes(f.world.uow).list(f.maker, "skus", f.sku, 0)
    assert len(rows["items"]) == 2 and len(rows["history"]) == 5


def test_code_package_category_and_party_entity_rules(flow):
    f = flow
    for payload in [
        code(scheme="NHSA_DRUG"),
        code(scheme="NHSA_ORGANIZATION"),
        code(packaging_id=uuid4()),
    ]:
        with pytest.raises(BusinessError):
            add(f, body=payload)
    p = approved(f)
    result = add(f, body=code(scheme="UDI_DI", packaging_id=UUID(p["id"])))
    row = CatalogCodes(f.world.uow).list(f.maker, "skus", f.sku, 0)["items"][0]
    assert row["id"] == result["id"] and row["unit_name"] == "箱"
    with pytest.raises(BusinessError):
        add(f, kind="parties")
    request = code(scheme="CUSTOM", namespace="ERP-A")
    key = str(uuid4())
    add(f, body=request, kind="parties", key=key)
    with pytest.raises(BusinessError) as exc:
        add(f, body=request, kind="parties", key=key, ctx=replace(f.maker, entity_ids=frozenset()))
    assert exc.value.status == 404
    assert (
        f.catalog.parties(f.maker, f.world.entity, "00123-AbC", 0)["items"][0]["id"]
        == f.party["id"]
    )
    add(f, body=code(scheme="CUSTOM", namespace="ERP-B"), kind="parties")


def test_code_concurrent_duplicate_and_commit_rollback(flow):
    f = flow

    def submit(_):
        try:
            return add(f)["id"]
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(submit, range(2)))
    assert values.count("EXTERNAL_CODE_EXISTS") == 1

    def abort():
        raise BusinessError("ABORT", "测试提交前权限失效", 403)

    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError):
            add(f, body=code(external_code="ROLLBACK"))
    finally:
        before_business_commit.reset(token)
    rows = CatalogCodes(f.world.uow).list(f.maker, "skus", f.sku, 0)
    assert len(rows["items"]) == len(rows["history"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"external_code": 123},
        {"external_code": "ab cd"},
        {"external_code": "a\x00b"},
        {"scheme": "MEITUAN"},
        {"namespace": "unexpected"},
        {"evidence_ref": ""},
    ],
)
def test_code_wire_validation(changes):
    with pytest.raises(ValidationError):
        CatalogCodeCreate(**{**code().model_dump(), **changes})


def test_party_legacy_fingerprint_and_strict_fields(flow):
    f = flow
    legacy = dict(
        entity_id=str(f.world.entity),
        code="OLD",
        name="老客户端",
        credit_code="OLD",
        kind="SUPPLIER",
        license_no="OLD",
        valid_from=str(f.day),
        valid_until=str(f.day),
        categories=["DEVICE"],
    )
    assert command_payload(PartyCreate(**legacy)) == legacy
    for fields in [
        dict(organization_type="INVALID"),
        dict(contact_phone="abc"),
        dict(short_name="a" * 101),
        dict(contact_name=123),
    ]:
        with pytest.raises(ValidationError):
            PartyCreate(**legacy, **fields)


def test_code_http_contract_read_scope_and_command_permission(api, flow):
    client, control = api
    f = flow
    add(f)
    path = f"/api/v1/catalog/skus/{f.sku}/codes"
    assert client.get(path).status_code == 403
    with control.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=array_append(permissions, "
                "'catalog.read') WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["external_code"] == "00123-AbC"
    assert client.get(path, params={"offset": -1}).status_code == 422
    assert client.get(f"/api/v1/catalog/skus/{uuid4()}/codes").status_code == 404
    assert client.post(path, json=code().model_dump(mode="json")).status_code == 403
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/catalog/{kind}/{id}/codes"]["post"]
    assert {p["name"] for p in operation["parameters"]} >= {
        "X-Context-Generation",
        "X-CSRF-Token",
        "Idempotency-Key",
        "Origin",
    }
    for path, filters in [
        ("products", {"category": "INVALID"}),
        ("parties", {"role": "INVALID", "entity_id": str(f.world.entity)}),
    ]:
        assert client.get(f"/api/v1/catalog/{path}", params=filters).status_code == 422
