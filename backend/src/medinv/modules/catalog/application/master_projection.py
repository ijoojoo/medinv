"""Product-grain projection, including collections without multiplying result rows."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.domain.master_schema import DETAIL_FIELDS, FIELDS, ROLE_LABELS
from medinv.platform.database.sql import SqlSession

PRODUCT_FIELDS = [f["key"] for f in FIELDS if f["binding_key"].startswith("product.")]
Related = dict[str, dict[str, list[dict[str, Any]]]]


def related_records(repo: SqlSession, ids: list[UUID]) -> Related:
    result: Related = {str(id): {} for id in ids}
    if not ids:
        return result
    queries = {
        "details": "SELECT * FROM med.product_master_detail WHERE product_id=ANY(:ids)",
        "skus": "SELECT product_id,id,code,base_unit,qty_scale FROM med.sku "
        "WHERE product_id=ANY(:ids) ORDER BY code,id",
        "roles": """SELECT r.*,o.name AS organization_name FROM med.product_organization_role r
          JOIN med.catalog_organization o ON (o.tenant_id,o.id)=(r.tenant_id,r.organization_id)
          WHERE r.product_id=ANY(:ids) ORDER BY r.role_code,o.name,r.id""",
        "certificates": """SELECT l.product_id,l.certificate_id,l.certificate_version,l.snapshot,
          l.business_use,c.version AS current_version,c.state AS current_state
          FROM med.product_certificate_link l JOIN med.product_certificate c
          ON (c.tenant_id,c.id)=(l.tenant_id,l.certificate_id)
          WHERE l.product_id=ANY(:ids) ORDER BY l.certificate_id""",
        "codes": """SELECT s.product_id,c.id,c.sku_id,c.packaging_id,c.version,
          c.scheme,c.namespace,c.external_code,
          COALESCE(pk.unit_name,s.base_unit) AS unit_name FROM med.catalog_code c
          JOIN med.sku s ON (s.tenant_id,s.id)=(c.tenant_id,c.sku_id)
          LEFT JOIN med.packaging pk ON (pk.tenant_id,pk.id)=(c.tenant_id,c.packaging_id)
          WHERE s.product_id=ANY(:ids) AND c.state='ACTIVE' ORDER BY c.created_at DESC,c.id DESC""",
        "prices": """SELECT s.product_id,s.id AS sku_id,s.code,s.base_unit,r.purchase_price,
          r.wholesale_price,r.retail_price,r.note,r.version
          FROM med.sku s JOIN med.sku_reference_price r
          ON (r.tenant_id,r.sku_id)=(s.tenant_id,s.id)
          WHERE s.product_id=ANY(:ids) ORDER BY s.code,s.id""",
    }
    for key, query in queries.items():
        for row in repo.rows(query, {"ids": ids}):
            result[str(row["product_id"])].setdefault(key, []).append(row)
    return result


def values_of(
    repo: SqlSession, row: dict[str, Any], related: Related | None = None
) -> dict[str, Any]:
    data = (related if related is not None else related_records(repo, [row["id"]]))[str(row["id"])]
    details, skus = data.get("details", []), data.get("skus", [])
    detail = details[0] if details else {}
    values = {key: row.get(key) for key in PRODUCT_FIELDS}
    values.update({f["key"]: detail.get(f["key"]) for f in DETAIL_FIELDS})
    values.update(detail.get("custom_values", {}))
    values["base_unit"] = detail.get("base_unit") or (
        skus[0]["base_unit"] if len(skus) == 1 else None
    )
    for key in ("device_class", "drug_sale_class"):
        if values[key] == "UNKNOWN":
            values[key] = None
    for field in ("regulatory_subject", "manufacturer", "service_provider"):

        def matches(role: str, group: str = field) -> bool:
            if group == "manufacturer":
                return role in {"MANUFACTURER", "CONTRACT_MANUFACTURER"}
            if group == "service_provider":
                return role == "SERVICE_PROVIDER"
            return role not in {"MANUFACTURER", "CONTRACT_MANUFACTURER", "SERVICE_PROVIDER"}

        values[field] = [
            {
                "organization_id": str(r["organization_id"]),
                "organization_name": r["organization_name"],
                "role_code": r["role_code"],
                "role_label": ROLE_LABELS[r["role_code"]],
                "evidence_ref": r["evidence_ref"],
                "source_certificate_id": str(r["source_certificate_id"])
                if r["source_certificate_id"]
                else None,
                "source_certificate_version": r["source_certificate_version"],
            }
            for r in data.get("roles", [])
            if matches(r["role_code"])
        ]
    values["product_certificates"] = data.get("certificates", [])
    values["reference_prices"] = data.get("prices", [])
    values["barcodes"] = [c for c in data.get("codes", []) if c["scheme"] == "BARCODE"]
    values["external_codes"] = [c for c in data.get("codes", []) if c["scheme"] != "BARCODE"]
    return dict(
        jsonable_encoder(values, custom_encoder={Decimal: lambda value: format(value, "f")})
    )
