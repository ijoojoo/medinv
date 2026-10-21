from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from qualification_helpers import review_parent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_api import api as api
from test_party_certificates import request as party_request
from test_party_certificates import revision as party_revision
from test_party_certificates import verification
from test_procurement import flow as flow
from test_product_certificates import request as product_request
from test_product_certificates import revision as product_revision

from medinv.modules.catalog.api.certificates import CertificateRevision
from medinv.modules.catalog.api.party_certificates import PartyCertificateRevision
from medinv.modules.catalog.application.attachments import (
    MAX_BYTES,
    CatalogAttachments,
    validate_file,
)
from medinv.modules.catalog.application.certificates import ProductCertificates
from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration
PDF = b"%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF\n"
PDF2 = PDF.replace(b"1.4", b"1.5")


def upload(f, kind="party", content=PDF, key=None, previous=None, parent=None):
    return CatalogAttachments(f.world.uow).upload(
        f.maker,
        kind,
        parent or UUID(getattr(f, kind)["id"]),
        "测试原件.pdf",
        content,
        "测试原件上传",
        key or str(uuid4()),
        previous,
    )


@pytest.mark.parametrize("kind", ["party", "product"])
def test_certificate_pins_immutable_file_and_revision_history(flow, kind):
    f = flow
    parent = UUID(getattr(f, kind)["id"])
    if kind == "product":
        f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:id", id=parent)
    files = CatalogAttachments(f.world.uow)
    certificates = (
        PartyCertificates(f.world.uow) if kind == "party" else ProductCertificates(f.world.uow)
    )
    request = party_request if kind == "party" else product_request
    revision = party_revision if kind == "party" else product_revision
    first = upload(f, kind)
    row = certificates.save(f.maker, parent, request(attachment_id=first["id"]), str(uuid4()))
    target = UUID(row["id"])
    # Existing original-reference requirement and independent review still apply.
    row = review_parent(certificates, f.reviewer, parent, target, verification(row), str(uuid4()))
    assert row["state"] == "VERIFIED"
    second = upload(f, kind, PDF2, previous=UUID(first["id"]))
    assert second["root_id"] == first["id"] and second["version"] == 2
    assert certificates.detail(f.maker, parent, target)["record"] == row
    revised = certificates.save(
        f.maker,
        parent,
        revision(row, attachment_id=second["id"]),
        str(uuid4()),
        target,
    )
    assert revised["state"] == "DRAFT"
    history = certificates.detail(f.maker, parent, target)["history"]
    assert history[0]["detail"]["before"]["attachment_id"] == first["id"]
    assert history[0]["detail"]["after"]["attachment_id"] == second["id"]
    assert files.download(f.maker, kind, parent, UUID(first["id"]))[1] == PDF
    assert files.download(f.maker, kind, parent, UUID(second["id"]))[1] == PDF2
    assert all("content" not in r for r in files.listing(f.maker, kind, parent)["items"])
    f.order()


def test_private_file_scope_and_link_cannot_cross_tenant_parent_or_entity(flow):
    f = flow
    service = CatalogAttachments(f.world.uow)
    parent = UUID(f.party["id"])
    row = upload(f)
    target = UUID(row["id"])
    for ctx in [
        replace(f.maker, tenant_id=uuid4()),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, permissions=frozenset()),
    ]:
        for operation in [
            lambda ctx=ctx: service.listing(ctx, "party", parent),
            lambda ctx=ctx: service.download(ctx, "party", parent, target),
            lambda ctx=ctx: service.upload(
                ctx, "party", parent, "x.pdf", PDF, "test", str(uuid4())
            ),
        ]:
            with pytest.raises(BusinessError):
                operation()
    with f.world.uow.open(replace(f.maker, tenant_id=uuid4()), readonly=True) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM med.catalog_attachment WHERE id=:id"), {"id": target}
            ).scalar_one()
            == 0
        )
    with pytest.raises(BusinessError, match="不属于"):
        service.download(f.maker, "product", UUID(f.product["id"]), target)
    f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:id", id=f.product["id"])
    with pytest.raises(BusinessError, match="不属于"):
        ProductCertificates(f.world.uow).save(
            f.maker, UUID(f.product["id"]), product_request(attachment_id=target), str(uuid4())
        )
    viewer = replace(f.maker, permissions=frozenset({"catalog.read"}))
    assert service.download(viewer, "party", parent, target)[1] == PDF
    with pytest.raises(BusinessError):
        service.upload(viewer, "party", parent, "x.pdf", PDF, "test", str(uuid4()))


def test_idempotency_concurrent_retry_and_stale_replacement(flow):
    f = flow
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: upload(f, key=key), range(2)))
    assert rows[0] == rows[1]
    with pytest.raises(BusinessError, match="幂等键"):
        upload(f, content=PDF2, key=key)
    target = UUID(rows[0]["id"])

    def replace_file(_):
        try:
            return upload(f, content=PDF2, previous=target)["version"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replace_file, range(2)))
    assert set(results) == {2, "VERSION_CONFLICT"}
    assert f.world.scalar("SELECT count(*) FROM med.catalog_attachment") == 2


def test_commit_failure_rolls_back_binary_metadata_audit_and_idempotency(flow):
    def abort():
        raise BusinessError("ABORT", "提交前授权失效", 403)

    before = flow.world.scalar("SELECT count(*) FROM med.idempotency_record")
    token = before_business_commit.set(abort)
    try:
        with pytest.raises(BusinessError, match="授权失效"):
            upload(flow)
    finally:
        before_business_commit.reset(token)
    assert flow.world.scalar("SELECT count(*) FROM med.catalog_attachment") == 0
    assert (
        flow.world.scalar(
            "SELECT count(*) FROM med.audit_event WHERE action='catalog.attachment_uploaded'"
        )
        == 0
    )
    assert flow.world.scalar("SELECT count(*) FROM med.idempotency_record") == before


def test_runtime_cannot_overwrite_or_delete_file_and_quota_is_atomic(flow, monkeypatch):
    row = upload(flow)
    for statement in [
        "UPDATE med.catalog_attachment SET filename='x.pdf' WHERE id=:id",
        "DELETE FROM med.catalog_attachment WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            flow.world.execute(statement, id=UUID(row["id"]))
    monkeypatch.setattr("medinv.modules.catalog.application.attachments.MAX_PARENT_BYTES", len(PDF))
    with pytest.raises(BusinessError, match="最多"):
        upload(flow)
    assert flow.world.scalar("SELECT count(*) FROM med.catalog_attachment") == 1


@pytest.mark.parametrize("kind", ["party", "product"])
def test_old_client_omission_preserves_attachment_but_explicit_null_clears(flow, kind):
    f = flow
    parent = UUID(getattr(f, kind)["id"])
    if kind == "product":
        f.world.execute("UPDATE med.product SET device_class='I' WHERE id=:id", id=parent)
    service = (
        PartyCertificates(f.world.uow) if kind == "party" else ProductCertificates(f.world.uow)
    )
    request = party_request if kind == "party" else product_request
    revision = party_revision if kind == "party" else product_revision
    model = PartyCertificateRevision if kind == "party" else CertificateRevision
    attachment = upload(f, kind)
    row = service.save(f.maker, parent, request(attachment_id=attachment["id"]), str(uuid4()))
    target = UUID(row["id"])
    payload = revision(row, note="旧客户端修订备注").model_dump(exclude={"attachment_id"})
    row = service.save(f.maker, parent, model.model_validate(payload), str(uuid4()), target)
    assert row["attachment_id"] == attachment["id"]
    row = service.save(f.maker, parent, revision(row, attachment_id=None), str(uuid4()), target)
    assert row["attachment_id"] is None
    assert (
        CatalogAttachments(f.world.uow).download(f.maker, kind, parent, UUID(attachment["id"]))[1]
        == PDF
    )


@pytest.mark.parametrize(
    "name,data,status",
    [
        ("x.pdf", b"", 413),
        pytest.param("x.pdf", b"x" * (MAX_BYTES + 1), 413, id="oversized"),
        ("../x.pdf", PDF, 422),
        ("C:\\x.pdf", PDF, 422),
        ("x\n.pdf", PDF, 422),
        ("x\u202e.pdf", PDF, 422),
        (" x.pdf", PDF, 422),
        ("x.html", PDF, 415),
        ("x.pdf", b"<html>script</html>", 415),
        ("x.png", PDF, 415),
        ("x.pdf", b"%PDF-1.4 broken", 415),
    ],
)
def test_reject_invalid_file(name, data, status):
    with pytest.raises(BusinessError) as error:
        validate_file(name, data)
    assert error.value.status == status


def enable_catalog(api, world):
    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permissions=:permissions WHERE tenant_id=:tenant"),
            {"permissions": list(ROLE_PERMISSIONS["OWNER"]), "tenant": world.ctx.tenant_id},
        )
    parent = world.scalar("SELECT product_id FROM med.sku WHERE id=:id", id=world.sku)
    return client, f"/api/v1/catalog/products/{parent}/attachments"


def test_binary_http_roundtrip_private_headers_and_revocation(api, world):
    client, root = enable_catalog(api, world)
    response = client.post(
        root,
        params={"filename": "原件.pdf", "reason": "软件测试"},
        content=PDF,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.json()
    row = response.json()["data"]
    assert row["sha256"] == sha256(PDF).hexdigest()
    path = root + f"/{row['id']}/content"
    result = client.get(path)
    assert result.content == PDF
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in result.headers["content-security-policy"]
    assert client.get(root).json()["data"]["items"][0]["id"] == row["id"]
    client.headers["X-Context-Generation"] = "2"
    assert client.get(path).status_code == 409
    client.headers["X-Context-Generation"] = "1"
    with api[1].begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.web_session SET revoked_at=now() WHERE tenant_id=:tenant"),
            {"tenant": world.ctx.tenant_id},
        )
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "header,value,status",
    [
        ("Origin", "https://invalid.example", 403),
        ("X-CSRF-Token", "wrong", 403),
        ("X-Context-Generation", "2", 409),
        ("Idempotency-Key", "short", 400),
        ("Content-Type", "text/html", 415),
    ],
)
def test_upload_auth_and_transport_headers(api, world, header, value, status):
    client, root = enable_catalog(api, world)
    response = client.post(
        root,
        params={"filename": "x.pdf", "reason": "test"},
        content=PDF,
        headers={"Content-Type": "application/octet-stream", header: value},
    )
    assert response.status_code == status
    assert world.scalar("SELECT count(*) FROM med.catalog_attachment") == 0


def test_streamed_size_limit_without_content_length(api, world):
    client, root = enable_catalog(api, world)

    def chunks():
        for _ in range(6):
            yield b"x" * (1024 * 1024)

    result = client.post(
        root,
        params={"filename": "x.pdf", "reason": "test"},
        content=chunks(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert result.status_code == 413
    assert world.scalar("SELECT count(*) FROM med.catalog_attachment") == 0
