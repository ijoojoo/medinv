"""Resolve free-entry reference values inside the product save transaction."""

from typing import Any
from uuid import UUID, uuid4

from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def inline_text(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= maximum
        and value.strip() not in {"—", "未知", "不适用"}
        and not any(ord(c) < 32 for c in value)
    )


def resolve_references(
    repo: SqlSession,
    ctx: TenantContext,
    product: UUID,
    values: dict[str, Any],
    certificate_type: str | None,
) -> None:
    # The caller holds the tenant schema lock. Explicit organization creation uses
    # the same lock so two concurrent product saves cannot add duplicate names.
    for field in ("regulatory_subject", "manufacturer", "service_provider"):
        result = []
        for entry in values.get(field) or []:
            if entry.get("organization_id"):
                result.append(entry)
                continue
            name = entry["organization_name"].strip()
            matches = repo.rows(
                "SELECT id,name,state FROM med.catalog_organization WHERE "
                "kind='ORGANIZATION' AND lower(btrim(name))=lower(:name)",
                {"name": name},
            )
            if len(matches) > 1:
                raise BusinessError(
                    "REFERENCE_AMBIGUOUS", "同名组织不止一个，请从候选项明确选择：" + name, 422
                )
            if matches and matches[0]["state"] != "ACTIVE":
                raise BusinessError(
                    "REFERENCE_INACTIVE", "该组织已停用，请核对后维护：" + name, 422
                )
            target = matches[0]["id"] if matches else uuid4()
            if not matches:
                repo.execute(
                    "INSERT INTO med.catalog_organization(tenant_id,id,name,kind,created_by) "
                    "VALUES(:t,:id,:name,'ORGANIZATION',:actor)",
                    {"t": ctx.tenant_id, "id": target, "name": name, "actor": ctx.principal_id},
                )
                audit(
                    repo,
                    ctx,
                    "organization",
                    target,
                    "master.organization.created",
                    {"name": name, "source": "product_input", "product_id": str(product)},
                )
            result.append({**entry, "organization_id": str(target), "organization_name": name})
        values[field] = result
    certificates = []
    for entry in values.get("product_certificates") or []:
        if entry.get("certificate_id"):
            certificates.append(entry)
            continue
        number = entry["number"].strip()
        matches = repo.rows(
            "SELECT id,version,state FROM med.product_certificate WHERE "
            "certificate_type=:kind AND lower(btrim(number))=lower(:number)",
            {"kind": certificate_type, "number": number},
        )
        if len(matches) > 1:
            raise BusinessError(
                "REFERENCE_AMBIGUOUS", "同编号证件不止一份，请从候选项明确选择：" + number, 422
            )
        if matches and matches[0]["state"] == "INACTIVE":
            raise BusinessError("REFERENCE_INACTIVE", "该证件已停用，请核对后维护：" + number, 422)
        if matches:
            target, version = matches[0]["id"], matches[0]["version"]
        else:
            if (
                repo.rows(
                    "SELECT count(*) AS n FROM med.product_certificate WHERE product_id=:p",
                    {"p": product},
                )[0]["n"]
                >= 100
            ):
                raise BusinessError("CERTIFICATE_LIMIT", "每个商品最多维护100份证照记录", 422)
            target, version = uuid4(), 1
            holders = values.get("regulatory_subject") or []
            holder = ""
            if len(holders) == 1:
                holder = repo.rows(
                    "SELECT name FROM med.catalog_organization WHERE id=:id",
                    {"id": holders[0]["organization_id"]},
                )[0]["name"]
            repo.execute(
                "INSERT INTO med.product_certificate(tenant_id,id,product_id,certificate_type,"
                "number,holder_name,validity_mode,created_by,updated_by,revision_editors) "
                "VALUES(:t,:id,:p,:kind,:number,:holder,'UNCONFIRMED',:actor,:actor,:editors)",
                {
                    "t": ctx.tenant_id,
                    "id": target,
                    "p": product,
                    "kind": certificate_type,
                    "number": number,
                    "holder": holder,
                    "actor": ctx.principal_id,
                    "editors": [ctx.principal_id],
                },
            )
            audit(
                repo,
                ctx,
                "product",
                product,
                "catalog.certificate_created",
                {
                    "certificate_id": str(target),
                    "number": number,
                    "source": "product_input",
                    "state": "DRAFT",
                    "validity_mode": "UNCONFIRMED",
                },
            )
        certificates.append(
            {
                "certificate_id": str(target),
                "certificate_version": version,
                "business_use": entry.get("business_use", False),
            }
        )
    values["product_certificates"] = certificates
