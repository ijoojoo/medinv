import json
from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.operations import NON_QUALIFICATION_FIELDS
from medinv.modules.catalog.api.party_profile import PARTY_PROFILE_FIELDS
from medinv.modules.catalog.api.schemas import (
    PROFILE_FIELDS,
    PartyRevision,
    ProductProfile,
    ProductRevision,
)
from medinv.modules.catalog.application.field_policy import record_policy
from medinv.modules.catalog.application.field_rules import validate_product
from medinv.modules.catalog.application.profile import command_payload
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Kind = Literal["product", "party"]
FIELDS = {
    "product": (
        "name",
        "manufacturer_name",
        "specification",
        "approval_number",
        "valid_from",
        "valid_until",
        "drug_sale_class",
        *PROFILE_FIELDS,
    ),
    "party": (
        "name",
        "kind",
        "license_no",
        "valid_from",
        "valid_until",
        "categories",
        *PARTY_PROFILE_FIELDS,
    ),
}


class CatalogMaintenance:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    @staticmethod
    def record(repo: SqlSession, kind: Kind, target: UUID, lock: bool = False) -> dict[str, Any]:
        rows = repo.rows(
            f"SELECT * FROM med.{kind} WHERE id=:id" + (" FOR UPDATE" if lock else ""),
            {"id": target},
        )
        if not rows:
            raise BusinessError("NOT_FOUND", "资料不可见或不存在", 404)
        return rows[0]

    def scope(self, ctx: TenantContext, kind: Kind, target: UUID, permission: str) -> None:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = self.record(SqlSession(session), kind, target)
            if kind == "party":
                ctx.require(permission, row["entity_id"])

    def detail(self, ctx: TenantContext, kind: Kind, target: UUID, offset: int) -> dict[str, Any]:
        self.scope(ctx, kind, target, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = self.record(repo, kind, target)
            row["version"] = str(row["version"])
            if kind == "party" and row["organization_id"]:
                organizations = repo.rows(
                    "SELECT id,name,legal_code,state FROM med.catalog_organization WHERE id=:id",
                    {"id": row["organization_id"]},
                )
                row["organization"] = organizations[0] if organizations else None
            if kind == "product":
                # Keep one product record even if later versions support multiple SKUs.
                row["skus"] = repo.rows(
                    "SELECT id,code,base_unit,qty_scale FROM med.sku "
                    "WHERE product_id=:id ORDER BY code,id",
                    {"id": target},
                )
            history = repo.rows(
                """SELECT id,created_by,created_at,action,reason,count(*) OVER() AS total
                FROM med.audit_event WHERE resource_type=:kind AND resource_id=:id
                  AND action IN ('catalog.created','party.created',
                    'catalog.reviewed','catalog.revised','catalog.business_updated','catalog.prices_updated',
                    'catalog.certificate_created','catalog.certificate_revised','catalog.certificate_reviewed')
                ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset""",
                {"kind": kind, "id": target, "offset": offset},
            )
            return dict(
                jsonable_encoder(
                    {
                        "record": row,
                        "field_policy": record_policy(repo, ctx, row)
                        if kind == "product" and row["category"] in {"DRUG", "DEVICE"}
                        else None,
                        "history": [
                            {
                                **{k: r[k] for k in ("id", "created_by", "created_at", "action")},
                                "detail": json.loads(r["reason"]),
                            }
                            for r in history
                        ],
                        "has_more": bool(history and offset + len(history) < history[0]["total"]),
                    }
                )
            )

    def revise(
        self,
        ctx: TenantContext,
        kind: Kind,
        target: UUID,
        body: ProductRevision | PartyRevision,
        key: str,
    ) -> dict[str, Any]:
        self.scope(ctx, kind, target, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            from medinv.modules.catalog.application.master_config import current_schema, schema_lock

            if kind == "product":
                schema_lock(repo, ctx)
            if kind == "party":
                entity = self.record(repo, kind, target)["entity_id"]
                ctx.require("catalog.write", entity)
                if not repo.rows(
                    "SELECT id FROM med.legal_entity WHERE id=:id AND status='ACTIVE' FOR SHARE",
                    {"id": entity},
                ):
                    raise BusinessError("ENTITY_UNAVAILABLE", "经营主体不可用", 404)
            row = self.record(repo, kind, target, lock=True)
            if kind == "product" and row.get("form_revision") is not None:
                raise BusinessError(
                    "MASTER_FORM_REQUIRED",
                    "此商品已使用版本化主数据，请通过商品主数据工作台修订",
                    409,
                )
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "资料已变化，请刷新后修订", 409)
            if kind == "product":
                assert isinstance(body, ProductRevision)
                if row["category"] != "DRUG" and body.drug_sale_class != "UNKNOWN":
                    raise BusinessError("DRUG_CLASS_INVALID", "仅药品可设置药品销售分类", 422)
            else:
                assert isinstance(body, PartyRevision)
            fields = FIELDS[kind]
            before = {k: row[k] for k in fields}
            submitted = body.model_dump()
            after = {
                k: before[k]
                if (
                    k in (*PROFILE_FIELDS, *PARTY_PROFILE_FIELDS) and k not in body.model_fields_set
                )
                or (kind == "party" and k == "kind" and submitted[k] is None)
                else submitted[k]
                for k in fields
            }
            if kind == "party" and after["kind"] != before["kind"] and after["kind"] != "BOTH":
                raise BusinessError(
                    "PARTY_ROLE_INVALID",
                    "可扩展为供应商及客户；已有业务身份不能在此缩减或替换",
                    422,
                )
            if kind == "product" and current_schema(repo)["id"] is not None:
                from medinv.modules.catalog.application.master import validation, values_of

                schema = current_schema(repo)["payload"]
                old_values = values_of(repo, row)
                scalar_keys = {
                    f["key"] for f in schema["fields"] if f["value_type"] != "collection"
                }
                updates = {
                    k: None if v == "UNKNOWN" else v for k, v in after.items() if k in scalar_keys
                }
                issues = validation(
                    repo,
                    ctx,
                    schema,
                    "drug" if row["category"] == "DRUG" else "device",
                    {**old_values, **updates},
                    updates,
                    old_values,
                    False,
                )
                if issues:
                    raise BusinessError(
                        "MASTER_VALIDATION", "；".join(i["message"] for i in issues), 422
                    )
            business_only = {k for k in after if after[k] != before[k]}.issubset(
                NON_QUALIFICATION_FIELDS[kind]
            )
            if kind == "product":
                try:
                    if (
                        row["device_class"] != "UNKNOWN"
                        and after["device_class"] != "UNKNOWN"
                        and (row["device_class"] == "I") != (after["device_class"] == "I")
                        and after["approval_number"] == row["approval_number"]
                    ):
                        raise BusinessError(
                            "PRODUCT_CREDENTIAL_CHANGED",
                            "器械类别已在备案与注册之间切换，请核对并填写对应的新证照编号",
                            422,
                        )
                    ProductProfile(**{k: after[k] for k in PROFILE_FIELDS}).validate_category(
                        row["category"]
                    )
                except ValueError as exc:
                    raise BusinessError(
                        "PRODUCT_PROFILE_INVALID", "商品专属资料与药品/器械类别不符", 422
                    ) from exc
            rules_version = (
                validate_product(
                    repo,
                    ctx,
                    {**row, **after},
                    previous=row,
                    only=NON_QUALIFICATION_FIELDS[kind] if business_only else None,
                )
                if kind == "product"
                else None
            )
            if before == after:
                raise BusinessError("CATALOG_UNCHANGED", "资料内容没有变化", 422)
            state_column = "approval_state" if kind == "product" else "state"
            if business_only:
                repo.execute(
                    f"""UPDATE med.{kind} SET {",".join(f"{k}=:{k}" for k in fields)},
                  version=version+1,updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                    {**after, "actor": ctx.principal_id, "id": target},
                )
                result = {
                    "id": str(target),
                    "state": row[state_column],
                    "version": str(row["version"] + 1),
                }
                audit(
                    repo,
                    ctx,
                    kind,
                    target,
                    "catalog.business_updated",
                    dict(
                        jsonable_encoder(
                            {"before": before, "after": after, "reason": body.reason, **result}
                        )
                    ),
                )
                return result
            editors = set() if row[state_column] == "APPROVED" else set(row["revision_editors"])
            editors.add(ctx.principal_id)
            repo.execute(
                f"""UPDATE med.{kind} SET {",".join(f"{k}=:{k}" for k in fields)},
                  {state_column}='DRAFT',reviewed_by=NULL,reviewed_at=NULL,
                  revision_editors=:editors,version=version+1,
                  updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                {**after, "id": target, "actor": ctx.principal_id, "editors": sorted(editors)},
            )
            result = {"id": str(target), "state": "DRAFT", "version": str(row["version"] + 1)}
            audit(
                repo,
                ctx,
                kind,
                target,
                "catalog.revised",
                dict(
                    jsonable_encoder(
                        {
                            "before": before,
                            "after": after,
                            "reason": body.reason,
                            "previous_state": row[state_column],
                            "previous_version": str(row["version"]),
                            "field_rules_version": rules_version,
                            **result,
                        }
                    )
                ),
            )
            return result

        return command(
            self.uow,
            ctx,
            f"catalog.{kind}.revise",
            str(target),
            key,
            command_payload(body),
            run,
        )
