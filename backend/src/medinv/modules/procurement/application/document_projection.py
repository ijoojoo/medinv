"""Display-only whitelist. Missing old snapshots are never filled from today's master."""

from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.application.document_master import definitions
from medinv.modules.catalog.domain.master_schema import regulatory_role
from medinv.modules.inventory.domain.decimal_values import money_for_posting
from medinv.modules.workspace.document_fields import FIELDS

MASTER_KEYS = {f["key"] for f in FIELDS if f["source"] == "master"} | {"product", "base_unit"}
LEGACY_SUBJECT_KEYS = {"authorization_holder", "registrant_name", "filing_holder"}


def capture(row: dict[str, Any]) -> dict[str, Any]:
    identity = row.get("master_identity", {})
    role = regulatory_role(row.get("category", "").lower(), row)
    values = {key: row.get(key, "") for key in MASTER_KEYS}
    values["product"] = row.get("name", "")
    values["code"] = row.get("product_code") or row.get("code", "")
    organizations = [
        {k: r[k] for k in ("organization_id", "name", "role_code") if k in r}
        for r in identity.get("organizations", [])
        if role
        and r["role_code"] in ({role, "REGISTRANT"} if role == "DEVICE_REGISTRANT" else {role})
    ]
    if identity:
        values["regulatory_subject"] = "；".join(dict.fromkeys(r["name"] for r in organizations))
    else:
        # The old product form used registrant_name for both device classes.
        key = {"DRUG": "authorization_holder", "DEVICE": "registrant_name"}.get(
            row.get("category", "")
        )
        values["regulatory_subject"] = row.get(key, "") if key else ""
    return dict(
        jsonable_encoder(
            dict(
                schema_version=3 if row.get("document_master") else 2,
                category=row.get("category", ""),
                device_class=row.get("device_class", ""),
                regulatory_subtype=row.get("regulatory_subtype"),
                regulatory_role=role,
                regulatory_subjects=organizations,
                data_version=row.get("data_version"),
                form_revision=row.get("form_revision"),
                values={**values, **row.get("document_master", {}).get("values", {})},
                field_status=row.get("document_master", {}).get("statuses", {}),
                field_permissions=row.get("document_master", {}).get("permissions", {}),
            )
        )
    )


def legacy_subject(snapshot: dict[str, Any], values: dict[str, Any]) -> None:
    """Map only the names already captured on that document; never query live MDM."""
    if "regulatory_subject" in values:
        return
    category = snapshot.get("category")
    key = None
    if category == "DRUG":
        key = "authorization_holder"
    elif category == "DEVICE":
        key = "filing_holder" if snapshot.get("device_class") == "I" else "registrant_name"
        # v1 snapshots of legacy device forms left filing_holder empty even when
        # the shared registrant/filing name was recorded. Modern identities must
        # not substitute a different legal role when their filer is missing.
        if (
            key == "filing_holder"
            and not values.get(key)
            and snapshot.get("form_revision") is None
            and values.get("registrant_name")
        ):
            key = "registrant_name"
    elif category == "HERB" and values.get("authorization_holder"):
        key = "authorization_holder"
    if key in values:
        values["regulatory_subject"] = values[key]


def subject_applicability(snapshot: dict[str, Any], value: Any) -> str | None:
    if snapshot.get("schema_version", 1) < 2:
        return None
    category = snapshot.get("category", "")
    if regulatory_role(category.lower(), snapshot):
        return None
    unconfirmed = (
        category == "DEVICE" and snapshot.get("device_class") not in {"I", "II", "III"}
    ) or (
        category in {"HERB", "HEALTH", "FSMP", "INFANT", "COSMETIC"}
        and not snapshot.get("regulatory_subtype")
    )
    if unconfirmed:
        return "待明确类别" if not value else None
    return "不适用"


def master_values(
    snapshot: dict[str, Any] | None,
    schema: dict[str, Any] | None = None,
    permissions: frozenset[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    values = snapshot.get("values", {}).copy() if snapshot else {}
    if snapshot:
        legacy_subject(snapshot, values)
    for old_key in LEGACY_SUBJECT_KEYS:
        values.pop(old_key, None)
    status = snapshot.get("field_status", {}).copy() if snapshot else {}
    extra = definitions(schema)
    for f in extra:
        key = f["key"]
        if key not in MASTER_KEYS and key not in values:
            status[key] = "历史未留存"
    for f in FIELDS:
        key = f["key"]
        if key not in MASTER_KEYS:
            continue
        if not snapshot or key not in values:
            status[key] = "历史未留存"
        elif (f["categories"] and snapshot["category"] not in f["categories"]) or (
            snapshot["category"] == "SERVICE" and key in {"manufacturer_name", "approval_number"}
        ):
            values[key] = None
            status[key] = "不适用"
        elif key == "regulatory_subject" and (
            message := subject_applicability(snapshot, values.get(key))
        ):
            status[key] = message
            if message == "不适用":
                values[key] = None
        elif values.get(key) in (None, ""):
            status[key] = "未维护"
    if permissions is not None:
        guards = {f["key"]: f["permission"] for f in extra if f["key"] not in MASTER_KEYS}
        stored_guards = snapshot.get("field_permissions", {}) if snapshot else {}
        for key, permission in list(guards.items()) + list(stored_guards.items()):
            if key not in MASTER_KEYS and permission not in permissions:
                values.pop(key, None)
                status[key] = "无权查看"
    return values, status


def project(
    line: dict[str, Any],
    order_line: dict[str, Any],
    stage: str,
    schema: dict[str, Any] | None = None,
    permissions: frozenset[str] | None = None,
) -> None:
    values, status = master_values(order_line.get("document_snapshot"), schema, permissions)
    for key in (
        "qty",
        "received_qty",
        "remaining_qty",
        "closed_qty",
        "pending_qty",
        "qualified_qty",
        "rejected_qty",
        "posted_qty",
        "custody_qty",
        "rejected_returned_qty",
        "lot_no",
        "mfg_date",
        "location",
        "reserved_qty",
        "fulfilled_qty",
    ):
        if key in line:
            values[key] = line[key]
    values["expiry_date"] = line.get("raw_expiry")
    if stage in {"orders", "sales_orders"}:
        package = order_line.get("package_snapshot") or {}
        values.update(
            qty=order_line.get("trade_qty") or order_line["qty"],
            base_qty=order_line["qty"],
            price=order_line["unit_price"],
            tax=order_line["tax_rate"],
            pack=package.get("unit_name") or values.get("base_unit"),
            conversion=(
                f"{package['denominator']} {package['unit_name']} = "
                f"{package['numerator']} {package['base_unit']}"
            )
            if package
            else "基本单位交易",
        )
        values["amount"] = money_for_posting(Decimal(values["qty"]) * Decimal(values["price"]))
        if not values.get("pack"):
            status["pack"] = "历史未留存"
    line["document_values"], line["document_status"] = values, status
    # Existing summaries must not masquerade current names as historical.
    line["name"] = values.get("product") or "历史商品名称未留存"


def project_detail(
    lines: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    schema: dict[str, Any] | None = None,
    permissions: frozenset[str] | None = None,
) -> None:
    orders = {str(line["id"]): line for line in lines}
    for line in lines:
        project(line, line, "orders", schema, permissions)
    for receipt in receipts:
        received = {str(line["id"]): line for line in receipt["lines"]}
        for line in receipt["lines"]:
            project(line, orders[str(line["order_line_id"])], "receiving", schema, permissions)
        for line in receipt["inspections"]:
            source = received[str(line["receipt_line_id"])]
            line.update(mfg_date=source["mfg_date"], raw_expiry=source["raw_expiry"])
            project(line, orders[str(source["order_line_id"])], "inspection", schema, permissions)
        for posting in receipt["postings"]:
            for line in posting["lines"]:
                source = received[str(line["receipt_line_id"])]
                line.update(
                    mfg_date=source["mfg_date"],
                    raw_expiry=source["raw_expiry"],
                    posted_qty=line["qualified_qty"]
                    if posting["state"] == "POSTED"
                    else Decimal(0),
                )
                project(line, orders[str(source["order_line_id"])], "posting", schema, permissions)


def public_documents(value: Any) -> Any:
    """Snapshots are storage evidence; only permission-filtered projections leave the API."""
    if isinstance(value, list):
        return [public_documents(v) for v in value]
    if isinstance(value, dict):
        return {k: public_documents(v) for k, v in value.items() if k != "document_snapshot"}
    return value
