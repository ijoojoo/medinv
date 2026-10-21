"""One pending change set per master record. Live rows remain usable until approval."""

import json
from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.qualification import BundleDiscard
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def pending(
    repo: SqlSession, kind: Literal["product", "party"], target: UUID
) -> dict[str, Any] | None:
    rows = repo.rows(
        "SELECT * FROM med.catalog_change_bundle WHERE kind=:k AND target_id=:id",
        {"k": kind, "id": target},
    )
    return rows[0] if rows else None


def visible_changes(ctx: TenantContext, schema: dict[str, Any], value: Any) -> Any:
    """Change previews and audit history obey the same field permissions as the form."""
    if isinstance(value, list):
        return [visible_changes(ctx, schema, item) for item in value]
    if not isinstance(value, dict):
        return value
    allowed = {f["key"] for f in schema["fields"] if f["read_permission"] in ctx.permissions}
    result = {}
    for key, child in value.items():
        if key == "values" and isinstance(child, dict):
            result[key] = {k: v for k, v in child.items() if k in allowed}
        elif key != "reference_prices" or key in allowed:
            result[key] = visible_changes(ctx, schema, child)
    return result


def editable(
    repo: SqlSession, kind: Literal["product", "party"], row: dict[str, Any]
) -> dict[str, Any]:
    draft = pending(repo, kind, row["id"])
    payload = (draft or {}).get("payload", {})
    return {
        **row,
        **payload.get("record", {}),
        **{k: v for k, v in payload.get("master", {}).get("values", {}).items() if k in row},
    }


def stage(
    repo: SqlSession,
    ctx: TenantContext,
    kind: Literal["product", "party"],
    row: dict[str, Any],
    area: str,
    value: dict[str, Any],
    reason: str,
    before_value: Any = None,
) -> dict[str, Any]:
    # Caller holds the master row lock: the parent version covers every child edit.
    draft = pending(repo, kind, row["id"])
    payload = dict(draft["payload"]) if draft else {}
    before = dict(draft["before_snapshot"]) if draft else {"record": jsonable_encoder(row)}
    if area == "certificates":
        payload[area] = {**payload.get(area, {}), **jsonable_encoder(value)}
    else:
        payload[area] = jsonable_encoder(value)
    if before_value is not None:
        if area == "certificates":
            before[area] = {**jsonable_encoder(before_value), **before.get(area, {})}
        else:
            before.setdefault(area, jsonable_encoder(before_value))
    editors = set(draft["editors"] if draft else []) | {ctx.principal_id}
    repo.execute(
        "INSERT INTO med.catalog_change_bundle(tenant_id,kind,target_id,payload,before_snapshot,"
        "editors,created_by,updated_by) VALUES(:t,:k,:id,"
        "CAST(:p AS jsonb),CAST(:b AS jsonb),:editors,:a,:a) "
        "ON CONFLICT(tenant_id,kind,target_id) DO UPDATE SET payload=EXCLUDED.payload,"
        "before_snapshot=EXCLUDED.before_snapshot,editors=EXCLUDED.editors,"
        "updated_by=:a,updated_at=now()",
        {
            "t": ctx.tenant_id,
            "k": kind,
            "id": row["id"],
            "p": json.dumps(payload),
            "b": json.dumps(before),
            "editors": sorted(editors),
            "a": ctx.principal_id,
        },
    )
    repo.execute(
        f"UPDATE med.{kind} SET version=version+1,updated_by=:a,updated_at=now() WHERE id=:id",
        {"a": ctx.principal_id, "id": row["id"]},
    )
    audit(
        repo,
        ctx,
        kind,
        row["id"],
        "catalog.change_staged",
        {
            "area": area,
            "reason": reason,
            "before": before.get(area),
            "after": payload[area],
            "version": str(row["version"] + 1),
        },
    )
    return {
        "id": str(row["id"]),
        "version": str(row["version"] + 1),
        "state": row.get("state", row.get("approval_state")),
        "has_changes": True,
    }


def certificate_rows(
    repo: SqlSession, kind: Literal["product", "party"], target: UUID, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    draft = pending(repo, kind, target)
    changes = (draft or {}).get("payload", {}).get("certificates", {})
    result = {str(r["id"]): r for r in rows}
    for cid, entry in changes.items():
        assert draft is not None
        # Pydantic restores date/UUID types from the JSON draft for existing presenters.
        from medinv.modules.catalog.api.certificates import CertificateValues
        from medinv.modules.catalog.api.party_certificates import PartyCertificateValues

        model = PartyCertificateValues if kind == "party" else CertificateValues
        values = model.model_validate(entry["values"]).model_dump()
        result[cid] = {
            **result.get(cid, {}),
            **values,
            "id": UUID(cid),
            f"{kind}_id": target,
            "version": entry["version"],
            "state": entry.get("state", "DRAFT"),
            "has_changes": True,
            "reviewed_by": None,
            "reviewed_at": None,
            "created_by": draft["created_by"],
            "revision_editors": draft["editors"],
        }
    return list(result.values())


def clear(repo: SqlSession, kind: Literal["product", "party"], target: UUID) -> None:
    repo.execute(
        "DELETE FROM med.catalog_change_bundle WHERE kind=:k AND target_id=:id",
        {"k": kind, "id": target},
    )


def product_draft_values(repo: SqlSession, target: UUID, values: dict[str, Any]) -> dict[str, Any]:
    draft = pending(repo, "product", target)
    payload = (draft or {}).get("payload", {})
    result = deepcopy({**values, **payload.get("master", {}).get("values", {})})
    changes = payload.get("certificates", {})
    for link in result.get("product_certificates") or []:
        change = changes.get(str(link.get("certificate_id")))
        if change:
            link["certificate_version"] = change["version"]
    for field in ("regulatory_subject", "manufacturer", "service_provider"):
        for relation in result.get(field) or []:
            change = changes.get(str(relation.get("source_certificate_id")))
            if change:
                relation["source_certificate_version"] = change["version"]
    return result


def apply_pending(
    repo: SqlSession,
    ctx: TenantContext,
    kind: Literal["product", "party"],
    row: dict[str, Any],
    uow: TenantUnitOfWork,
) -> None:
    """Called only inside the single parent review transaction; errors roll everything back."""
    from medinv.modules.catalog.api.certificates import CertificateValues
    from medinv.modules.catalog.api.party_certificates import PartyCertificateValues
    from medinv.modules.catalog.application.maintenance import FIELDS

    draft = pending(repo, kind, row["id"])
    payload = draft["payload"] if draft else {}
    record = payload.get("record", {})
    if record:
        # Columns originate from the server's validated DTO, never arbitrary client identifiers.
        columns = [k for k in record if k in FIELDS[kind]]
        repo.execute(
            f"UPDATE med.{kind} SET " + ",".join(f"{k}=:{k}" for k in columns) + " WHERE id=:id",
            {**record, "id": row["id"]},
        )
    changed_certificates = {}
    for cid, entry in payload.get("certificates", {}).items():
        model = PartyCertificateValues if kind == "party" else CertificateValues
        values = model.model_validate(entry["values"]).model_dump()
        old = repo.rows(
            f"SELECT * FROM med.{kind}_certificate WHERE {kind}_id=:p AND id=:id FOR UPDATE",
            {"p": row["id"], "id": UUID(cid)},
        )
        if old and int(old[0]["version"]) != int(entry["base_version"]):
            raise BusinessError("VERSION_CONFLICT", "证件有效版本已变化，请重新核对变更", 409)
        columns = list(model.model_fields)
        params = {
            **values,
            "id": UUID(cid),
            "p": row["id"],
            "t": ctx.tenant_id,
            "a": ctx.principal_id,
            "v": entry["version"],
            "state": entry.get("state", "DRAFT"),
        }
        if old:
            repo.execute(
                f"UPDATE med.{kind}_certificate SET "
                + ",".join(f"{k}=:{k}" for k in columns)
                + ",state=:state,version=:v,updated_by=:a,updated_at=now() WHERE id=:id",
                params,
            )
        else:
            repo.execute(
                f"INSERT INTO med.{kind}_certificate(tenant_id,id,{kind}_id,created_by,"
                "updated_by,version,state,"
                + ",".join(columns)
                + ") VALUES(:t,:id,:p,:a,:a,:v,:state,"
                + ",".join(":" + k for k in columns)
                + ")",
                params,
            )
        changed_certificates[cid] = int(entry["version"])
    if kind == "product" and payload.get("master"):
        from medinv.modules.catalog.api.master import MasterSave
        from medinv.modules.catalog.application.master import ProductMaster

        body = deepcopy(payload["master"])
        body["values"] = product_draft_values(repo, row["id"], body["values"])
        body["expected_version"] = row["version"]
        for link in body["values"].get("product_certificates") or []:
            if str(link.get("certificate_id")) in changed_certificates:
                link["certificate_version"] = changed_certificates[str(link["certificate_id"])]
        ProductMaster(uow).insert(repo, ctx, MasterSave.model_validate(body), stage_changes=False)
    if kind == "party" and "scopes" in payload:
        repo.execute(
            "INSERT INTO med.party_scope_profile(tenant_id,party_id,entity_id,payload,reviewed_by) "
            "VALUES(:t,:p,:e,CAST(:payload AS jsonb),:a) "
            "ON CONFLICT(tenant_id,party_id) DO UPDATE SET "
            "payload=EXCLUDED.payload,version=party_scope_profile.version+1,"
            "reviewed_by=:a,reviewed_at=now()",
            {
                "t": ctx.tenant_id,
                "p": row["id"],
                "e": row["entity_id"],
                "payload": json.dumps(payload["scopes"]),
                "a": ctx.principal_id,
            },
        )
    # Certificate approval is an effect of master approval, never a second user task.
    from medinv.modules.catalog.application.certificates import (
        compatibility as product_compatibility,
    )
    from medinv.modules.catalog.application.party_certificates import PartyCertificates
    from medinv.modules.catalog.application.qualification_rules import settings

    config = settings(repo)
    parent = repo.rows(f"SELECT * FROM med.{kind} WHERE id=:p", {"p": row["id"]})[0]
    children = repo.rows(
        f"SELECT * FROM med.{kind}_certificate WHERE {kind}_id=:p AND state='DRAFT'",
        {"p": row["id"]},
    )
    for child in children:
        if config["certificate_evidence_required"] and not all(
            child[k] for k in ("holder_name", "issuer", "evidence_ref")
        ):
            raise BusinessError("CERTIFICATE_NOT_READY", "请补齐证件主体、签发机关和原件凭据", 422)
        if kind == "party":
            PartyCertificates.check_business_uses(parent, child["business_uses"])
            if (
                config["party_certificate_validity_confirmed"]
                and child["validity_mode"] == "UNCONFIRMED"
            ):
                raise BusinessError("CERTIFICATE_NOT_READY", "请按原件核实证件有效期", 422)
            if (
                config["authorization_contact_required"]
                and child["certificate_type"] == "AUTHORIZATION"
                and not all(
                    child[k]
                    for k in ("authorized_name", "authorized_phone", "authorization_duties")
                )
            ):
                raise BusinessError(
                    "CERTIFICATE_NOT_READY", "请补齐授权联系人、联系电话和授权事项", 422
                )
        else:
            if (
                config["product_certificate_type_check"]
                and product_compatibility(parent, child["certificate_type"]) != "MATCH"
            ):
                raise BusinessError(
                    "CERTIFICATE_NOT_READY", "请先核定商品分类，证件类型须与商品类别相符", 422
                )
            if config["product_certificate_dates"] and (
                child["validity_mode"] == "UNCONFIRMED"
                or (
                    child["certificate_type"] in config["product_fixed_period_types"]
                    and child["validity_mode"] != "FIXED"
                )
            ):
                raise BusinessError(
                    "CERTIFICATE_NOT_READY",
                    "请按原件核实有效期；当前规则要求此类证件填写起止日期",
                    422,
                )
        audit(
            repo,
            ctx,
            kind,
            row["id"],
            "catalog.certificate_reviewed",
            {
                "certificate_id": str(child["id"]),
                "certificate_domain": kind,
                "source": "parent_review",
                "decision": "VERIFY",
                "before": jsonable_encoder(child),
                "after": jsonable_encoder({**child, "state": "VERIFIED"}),
            },
        )
    repo.execute(
        f"UPDATE med.{kind}_certificate SET state='VERIFIED',reviewed_by=:a,reviewed_at=now(),"
        f"revision_editors=ARRAY[]::uuid[] WHERE {kind}_id=:p AND state='DRAFT'",
        {"p": row["id"], "a": ctx.principal_id},
    )
    if kind == "product":
        # Refresh this reviewed product only. Shared evidence must retain its explicit version.
        repo.execute(
            "UPDATE med.product_certificate_link l SET certificate_version=c.version,"
            "snapshot=to_jsonb(c) FROM med.product_certificate c "
            "WHERE (l.tenant_id,l.certificate_id)=(c.tenant_id,c.id) "
            "AND l.product_id=:p AND (c.product_id=:p OR c.version=l.certificate_version)",
            {"p": row["id"]},
        )


def stage_certificate(
    repo: SqlSession,
    ctx: TenantContext,
    kind: Literal["product", "party"],
    parent: dict[str, Any],
    target: UUID,
    values: dict[str, Any],
    old: dict[str, Any] | None,
    reason: str,
    state: str = "DRAFT",
) -> dict[str, Any]:
    draft = pending(repo, kind, parent["id"])
    prior = (draft or {}).get("payload", {}).get("certificates", {}).get(str(target))
    base = prior["base_version"] if prior else old["version"] if old else 0
    version = int(prior["version"] if prior else base) + 1
    stage(
        repo,
        ctx,
        kind,
        parent,
        "certificates",
        {str(target): {"values": values, "base_version": base, "version": version, "state": state}},
        reason,
        {str(target): old},
    )
    audit(
        repo,
        ctx,
        kind,
        parent["id"],
        "catalog.certificate_revised" if old else "catalog.certificate_created",
        {
            "certificate_id": str(target),
            "certificate_domain": kind,
            "reason": reason,
            "pending": True,
            "before": jsonable_encoder(old),
            "after": jsonable_encoder({**values, "state": state}),
            "version": str(version),
        },
    )
    return next(
        r
        for r in certificate_rows(repo, kind, parent["id"], [old] if old else [])
        if r["id"] == target
    )


class ChangeBundles:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def detail(
        self, ctx: TenantContext, kind: Literal["product", "party"], target: UUID
    ) -> dict[str, Any]:
        from medinv.modules.catalog.application.maintenance import CatalogMaintenance

        CatalogMaintenance(self.uow).scope(ctx, kind, target, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = CatalogMaintenance.record(repo, kind, target)
            draft = pending(repo, kind, target)
            if draft and kind == "product":
                from medinv.modules.catalog.application.master_config import current_schema

                schema = current_schema(repo)["payload"]
                draft = visible_changes(ctx, schema, draft)
            return dict(
                jsonable_encoder(
                    {
                        "version": str(row["version"]),
                        "has_changes": bool(draft),
                        "draft": draft,
                        "active_record": row,
                    }
                )
            )

    def discard(
        self,
        ctx: TenantContext,
        kind: Literal["product", "party"],
        target: UUID,
        body: BundleDiscard,
        key: str,
    ) -> dict[str, Any]:
        from medinv.modules.catalog.application.maintenance import CatalogMaintenance

        CatalogMaintenance(self.uow).scope(ctx, kind, target, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            row = CatalogMaintenance.record(repo, kind, target, lock=True)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "资料或变更草稿已变化，请刷新", 409)
            draft = pending(repo, kind, target)
            if not draft:
                raise BusinessError("NO_DRAFT", "没有待处理变更", 422)
            clear(repo, kind, target)
            repo.execute(f"UPDATE med.{kind} SET version=version+1 WHERE id=:id", {"id": target})
            audit(
                repo,
                ctx,
                kind,
                target,
                "catalog.change_discarded",
                {"reason": body.reason, "draft": jsonable_encoder(draft)},
            )
            return {"version": str(row["version"] + 1), "has_changes": False}

        return command(
            self.uow,
            ctx,
            "catalog.change.discard",
            f"{kind}:{target}",
            key,
            body.model_dump(mode="json"),
            run,
        )
