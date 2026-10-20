from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_product_master import body
from test_product_master import master as master

from medinv.modules.catalog.api.master import OrganizationCreate
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def inline(name="输入厂家", number="国药准字TEST001"):
    return body(
        "drug",
        regulatory_subject=[{"organization_name": name, "role_code": "DRUG_MAH"}],
        manufacturer=[{"organization_name": name, "role_code": "MANUFACTURER"}],
        product_certificates=[{"number": number, "business_use": True}],
    )


def test_inline_save_resolves_reuses_and_does_not_approve(master):
    service, config, ctx, world = master
    request = inline()
    key = str(uuid4())
    result = service.save(ctx, request, key)
    assert service.save(ctx, request, key) == result
    detail = service.detail(ctx, UUID(result["id"]))
    values = detail["values"]
    assert (
        values["regulatory_subject"][0]["organization_id"]
        == values["manufacturer"][0]["organization_id"]
    )
    cert = values["product_certificates"][0]
    assert cert["snapshot"]["state"] == "DRAFT"
    assert cert["snapshot"]["validity_mode"] == "UNCONFIRMED"
    assert detail["approval_state"] == "DRAFT"
    assert cert["business_use"] is True
    next_result = service.save(ctx, inline(" 输入厂家 "), str(uuid4()))
    next_values = service.detail(ctx, UUID(next_result["id"]))["values"]
    assert (
        next_values["manufacturer"][0]["organization_id"]
        == values["manufacturer"][0]["organization_id"]
    )
    assert next_values["product_certificates"][0]["certificate_id"] == cert["certificate_id"]
    assert world.scalar("SELECT count(*) FROM med.catalog_organization WHERE name='输入厂家'") == 1
    assert (
        world.scalar("SELECT count(*) FROM med.product_certificate WHERE number='国药准字TEST001'")
        == 1
    )


def test_validation_has_no_dictionary_side_effects_and_rejects_malformed(master):
    service, config, ctx, world = master
    checked = service.validate(ctx, inline())
    assert not checked["valid"]
    assert any("先保存" in issue["message"] for issue in checked["issues"])
    assert not service.organizations(ctx)["items"]
    assert not service.certificates(ctx)["items"]
    for bad in (" ", "x" * 201, "厂家\n伪造值"):
        with pytest.raises(BusinessError):
            service.save(ctx, inline(bad), str(uuid4()))
    assert not service.organizations(ctx)["items"]


def test_ambiguous_name_rolls_back_all_new_values(master):
    service, config, ctx, world = master
    for code in ("ID1", "ID2"):
        service.create_organization(
            ctx, OrganizationCreate(name="同名厂家", legal_code=code), str(uuid4())
        )
    before = world.scalar("SELECT count(*) FROM med.product")
    with pytest.raises(BusinessError, match="同名组织"):
        service.save(ctx, inline("同名厂家"), str(uuid4()))
    assert world.scalar("SELECT count(*) FROM med.product") == before
    assert not service.certificates(ctx)["items"]


def test_concurrent_inline_saves_share_one_identity(master):
    service, config, ctx, world = master

    def save(index):
        return service.save(ctx, inline("并发厂家", "并发证件"), str(uuid4()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert len({result["id"] for result in results}) == 2
    assert world.scalar("SELECT count(*) FROM med.catalog_organization WHERE name='并发厂家'") == 1
    assert world.scalar("SELECT count(*) FROM med.product_certificate WHERE number='并发证件'") == 1


def test_permissions_and_tenant_isolation(master):
    service, config, ctx, world = master
    service.save(ctx, inline(), str(uuid4()))
    other = replace(ctx, tenant_id=uuid4())
    assert service.organizations(other, "输入厂家")["items"] == []
    assert service.certificates(other, "TEST001")["items"] == []
    reader = replace(ctx, permissions=frozenset({"catalog.read"}))
    with pytest.raises(BusinessError):
        service.save(reader, inline("无权限新增"), str(uuid4()))
    assert not service.organizations(ctx, "无权限新增")["items"]
