from typing import Any

from pydantic import BaseModel

from medinv.modules.catalog.api.party_profile import PARTY_PROFILE_FIELDS
from medinv.modules.catalog.api.schemas import PROFILE_FIELDS
from medinv.modules.catalog.application.master_config import business_rules_changed, current_schema
from medinv.modules.catalog.application.master_projection import related_records
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError

PROFILE_SELECT = (
    ",".join(f"p.{field}" for field in PROFILE_FIELDS) + ",p.form_revision,p.data_version"
)


def project_business_identity(
    repo: SqlSession, rows: list[dict[str, Any]], *, enforce_rules: bool = True
) -> None:
    """Project confirmed identities at the transaction boundary, never rewrite old snapshots."""
    modern = [row for row in rows if row.get("form_revision") is not None]
    current = current_schema(repo) if modern and enforce_rules else None
    checked = set()
    for row in modern:
        pair = (row["form_revision"], row["category"].lower())
        if current and pair not in checked:
            if business_rules_changed(repo, pair[0], pair[1], current):
                raise BusinessError(
                    "MASTER_REVALIDATION_REQUIRED",
                    "商品规则已变更，请按新规则补齐资料并重新审核后办理业务",
                    422,
                )
            checked.add(pair)
    related = related_records(repo, list({row["product_id"] for row in modern}))
    for row in modern:
        data = related[str(row["product_id"])]
        roles = data.get("roles", [])
        details = data.get("details", [])
        row["regulatory_subtype"] = details[0]["regulatory_subtype"] if details else None
        certificates = data.get("certificates", [])
        chosen = [c for c in certificates if c["business_use"]]
        row["manufacturer_name"] = "；".join(
            dict.fromkeys(
                r["organization_name"]
                for r in roles
                if r["role_code"] in {"MANUFACTURER", "CONTRACT_MANUFACTURER"}
            )
        )
        row["approval_number"] = chosen[0]["snapshot"]["number"] if len(chosen) == 1 else ""
        row["master_identity"] = {
            "product_id": str(row["product_id"]),
            "data_version": row["data_version"],
            "form_revision": row["form_revision"],
            "regulatory_subtype": row["regulatory_subtype"],
            "organizations": [
                {
                    "organization_id": str(r["organization_id"]),
                    "name": r["organization_name"],
                    "role_code": r["role_code"],
                    "evidence_ref": r["evidence_ref"],
                }
                for r in roles
            ],
            "business_certificate": chosen[0]["snapshot"] if len(chosen) == 1 else None,
        }


def command_payload(body: BaseModel) -> dict[str, Any]:
    """Omitted new fields retain the legacy request fingerprint across upgrades."""
    payload = body.model_dump(mode="json")
    for field in (*PROFILE_FIELDS, *PARTY_PROFILE_FIELDS, "organization", "kind"):
        if field not in body.model_fields_set:
            payload.pop(field, None)
    return payload


def product_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    fields = ("name", "specification", "approval_number", "manufacturer_name", *PROFILE_FIELDS)
    return {k: row[k] for k in fields} | {
        k: row[k] for k in ("category", "drug_sale_class", "master_identity") if k in row
    }
