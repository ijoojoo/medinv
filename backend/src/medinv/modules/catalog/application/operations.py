import json
import re
from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.operations import MasterKind, OperationsUpdate
from medinv.modules.catalog.application.dictionaries import validate_dictionary
from medinv.modules.catalog.application.field_policy import record_policy
from medinv.modules.catalog.application.field_rules import validate_product
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

COMMON_FIELDS = {
    "recommendation_level": 60,
    "abc_class": 60,
    "abc_secondary_class": 60,
    "business_note": 500,
    "search_alias": 200,
}
PARTY_FIELDS = {
    **COMMON_FIELDS,
    "search_alias": 100,
    "short_name": 100,
    "contact_name": 100,
    "contact_phone": 50,
    "delivery_address": 300,
}


class CatalogOperations:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.master = CatalogMaintenance(uow)

    def get(self, ctx: TenantContext, kind: MasterKind, target: UUID) -> dict[str, Any]:
        table: Literal["product", "party"] = "product" if kind == "products" else "party"
        self.master.scope(ctx, table, target, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = self.master.record(repo, table, target)
            fields = COMMON_FIELDS if kind == "products" else PARTY_FIELDS
            events = repo.rows(
                """SELECT created_at,created_by,reason FROM med.audit_event
                WHERE resource_type=:kind AND resource_id=:id AND action='catalog.business_updated'
                ORDER BY created_at DESC,id LIMIT 30""",
                {"kind": table, "id": target},
            )
            return dict(
                jsonable_encoder(
                    {
                        "version": str(row["version"]),
                        "state": row["approval_state" if kind == "products" else "state"],
                        "values": {k: row[k] for k in fields},
                        "field_policy": record_policy(repo, ctx, row)
                        if kind == "products" and row["category"] in {"DRUG", "DEVICE"}
                        else None,
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in events],
                    }
                )
            )

    def update(
        self, ctx: TenantContext, kind: MasterKind, target: UUID, body: OperationsUpdate, key: str
    ) -> dict[str, Any]:
        table: Literal["product", "party"] = "product" if kind == "products" else "party"
        self.master.scope(ctx, table, target, "catalog.write")
        fields = COMMON_FIELDS if kind == "products" else PARTY_FIELDS
        if not set(body.values).issubset(fields) or any(
            len(v) > fields[k] for k, v in body.values.items()
        ):
            raise BusinessError(
                "OPERATIONAL_FIELD_INVALID", "字段不属于当前档案经营信息或长度超限", 422
            )
        if "contact_phone" in body.values and not re.fullmatch(
            r"[0-9+()（） .转分机-]*", body.values["contact_phone"]
        ):
            raise BusinessError("OPERATIONAL_FIELD_INVALID", "联系电话格式不正确", 422)

        def run(repo: SqlSession) -> dict[str, Any]:
            from medinv.modules.catalog.application.master_config import current_schema, schema_lock

            if kind == "products":
                schema_lock(repo, ctx)
            if kind == "parties":
                entity = self.master.record(repo, table, target)["entity_id"]
                ctx.require("catalog.write", entity)
                if not repo.rows(
                    "SELECT id FROM med.legal_entity WHERE id=:id AND status='ACTIVE' FOR SHARE",
                    {"id": entity},
                ):
                    raise BusinessError("ENTITY_UNAVAILABLE", "经营主体不可用", 404)
            row = self.master.record(repo, table, target, lock=True)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "资料已变化，请重新读取后保存经营信息", 409)
            before = {k: row[k] for k in body.values}
            after: dict[str, Any] = {str(k): v for k, v in body.values.items()}
            if (
                kind == "products"
                and "search_alias" in after
                and after["search_alias"] != row["search_alias"]
            ):
                if row["form_revision"] is not None:
                    raise BusinessError(
                        "MASTER_FORM_REQUIRED", "助记码请从商品主数据工作台修改", 409
                    )
                from medinv.modules.catalog.application.master import validation, values_of

                schema = current_schema(repo)["payload"]
                old_values = values_of(repo, row)
                issues = validation(
                    repo,
                    ctx,
                    schema,
                    "drug" if row["category"] == "DRUG" else "device",
                    {**old_values, "search_alias": after["search_alias"]},
                    {"search_alias": after["search_alias"]},
                    old_values,
                    False,
                )
                if issues:
                    raise BusinessError(
                        "MASTER_VALIDATION", "；".join(i["message"] for i in issues), 422
                    )
            if before == after:
                raise BusinessError("CATALOG_UNCHANGED", "经营信息没有变化", 422)
            if (
                kind == "products"
                and "search_alias" in body.values
                and row["category"] in {"DRUG", "DEVICE"}
            ):
                validate_product(repo, ctx, {**row, **after}, previous=row, only={"search_alias"})
            for field in ("recommendation_level", "abc_class", "abc_secondary_class"):
                if field in body.values:
                    validate_dictionary(
                        repo,
                        ctx,
                        "abc_class" if field.startswith("abc_") else field,
                        body.values[field],
                        row[field],
                    )
            repo.execute(
                f"""UPDATE med.{table} SET {",".join(f"{k}=:{k}" for k in after)},
                version=version+1,updated_by=:actor,updated_at=clock_timestamp() WHERE id=:id""",
                {**after, "actor": ctx.principal_id, "id": target},
            )
            state = row["approval_state" if kind == "products" else "state"]
            audit(
                repo,
                ctx,
                table,
                target,
                "catalog.business_updated",
                {
                    "before": before,
                    "after": after,
                    "reason": body.reason,
                    "state": state,
                    "version": str(row["version"] + 1),
                },
            )
            return {"id": str(target), "version": str(row["version"] + 1), "state": state}

        return command(
            self.uow,
            ctx,
            f"catalog.{table}.operations",
            str(target),
            key,
            body.model_dump(mode="json"),
            run,
        )
