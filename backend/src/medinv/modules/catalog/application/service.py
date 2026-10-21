from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.party_profile import PARTY_PROFILE_FIELDS
from medinv.modules.catalog.api.schemas import PROFILE_FIELDS, PartyCreate, ProductCreate, Review
from medinv.modules.catalog.application.code_rules import resolve_product_codes
from medinv.modules.catalog.application.dictionaries import validate_dictionary
from medinv.modules.catalog.application.field_rules import validate_product
from medinv.modules.catalog.application.party_identity import resolve_party_identity
from medinv.modules.catalog.application.profile import command_payload
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class CatalogService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def products(
        self, ctx: TenantContext, search: str, offset: int, category: str = "", state: str = ""
    ) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                """SELECT p.*,s.id AS sku_id,s.code AS sku_code,s.base_unit,s.qty_scale,
                EXISTS(SELECT 1 FROM med.catalog_change_bundle b
                  WHERE b.kind='product' AND b.target_id=p.id) AS has_changes,
                count(*) OVER() AS total FROM med.product p
                JOIN med.sku s ON (s.tenant_id,s.product_id)=(p.tenant_id,p.id)
                WHERE (:category='' OR p.category=:category)
                AND (:state='' OR p.approval_state=:state OR (:state='DRAFT' AND EXISTS(
                  SELECT 1 FROM med.catalog_change_bundle b
                  WHERE b.kind='product' AND b.target_id=p.id)))
                AND (strpos(lower(concat_ws(' ',p.name,p.code,s.code,p.generic_name,
                  p.search_alias,p.trade_name,p.manufacturer_name,p.approval_number,p.device_model)),
                lower(:search))>0 OR EXISTS (SELECT 1 FROM med.catalog_code c
                  WHERE c.sku_id=s.id AND c.state='ACTIVE'
                  AND strpos(lower(c.external_code),lower(:search))>0))
                ORDER BY p.created_at DESC,p.id,s.id LIMIT 50 OFFSET :offset""",
                {"search": search.strip(), "offset": offset, "category": category, "state": state},
            )
            return {
                "items": jsonable_encoder([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def parties(
        self,
        ctx: TenantContext,
        entity: UUID,
        search: str,
        offset: int,
        role: str = "",
        state: str = "",
    ) -> dict[str, Any]:
        ctx.require("catalog.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT p.*,EXISTS(SELECT 1 FROM med.catalog_change_bundle b
                  WHERE b.kind='party' AND b.target_id=p.id) AS has_changes,
                count(*) OVER() AS total FROM med.party p
                WHERE entity_id=:e AND (:role='' OR kind=:role OR kind='BOTH')
                AND (:state='' OR p.state=:state OR (:state='DRAFT' AND EXISTS(
                  SELECT 1 FROM med.catalog_change_bundle b
                  WHERE b.kind='party' AND b.target_id=p.id)))
                AND (strpos(lower(concat_ws(' ',name,code,short_name,search_alias,credit_code,
                  contact_name,contact_phone)),lower(:search))>0
                  OR EXISTS (SELECT 1 FROM med.catalog_code c WHERE c.party_id=p.id
                    AND c.state='ACTIVE' AND strpos(lower(c.external_code),lower(:search))>0))
                ORDER BY p.created_at DESC,p.id LIMIT 50 OFFSET :offset""",
                {
                    "e": entity,
                    "search": search.strip(),
                    "offset": offset,
                    "role": role,
                    "state": state,
                },
            )
            return {
                "items": jsonable_encoder([{**r, "version": str(r["version"])} for r in rows]),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def create_product(self, ctx: TenantContext, body: ProductCreate, key: str) -> dict[str, Any]:
        ctx.require("catalog.write")

        return command(
            self.uow,
            ctx,
            "catalog.product.create",
            "new",
            key,
            command_payload(body),
            lambda repo: self.insert_product(repo, ctx, body),
        )

    def insert_product(
        self, repo: SqlSession, ctx: TenantContext, body: ProductCreate
    ) -> dict[str, Any]:
        from medinv.modules.catalog.application.master import validation
        from medinv.modules.catalog.application.master_config import current_schema, schema_lock

        schema_lock(repo, ctx)
        config = current_schema(repo)
        if config["id"] is not None:
            allowed = {
                f["key"] for f in config["payload"]["fields"] if f["value_type"] != "collection"
            }
            mapped = {
                k: None if v == "UNKNOWN" else v
                for k, v in body.model_dump().items()
                if k in allowed
            }
            issues = validation(
                repo,
                ctx,
                config["payload"],
                "drug" if body.category == "DRUG" else "device",
                mapped,
                mapped,
                None,
                False,
            )
            if issues:
                raise BusinessError(
                    "MASTER_VALIDATION", "；".join(i["message"] for i in issues[:10]), 422
                )
        rules_version = validate_product(repo, ctx, body.model_dump())
        validate_dictionary(repo, ctx, "base_unit", body.base_unit)
        code, sku_code = resolve_product_codes(repo, ctx, body.category, body.code, body.sku_code)
        pid, sid = uuid4(), uuid4()
        p = {
            **body.model_dump(),
            "code": code,
            "sku_code": sku_code,
            "t": ctx.tenant_id,
            "actor": ctx.principal_id,
            "p": pid,
            "s": sid,
        }
        result = repo.rows(
            f"""INSERT INTO med.product(tenant_id,id,created_by,updated_by,code,name,
            category,
            approval_state,manufacturer_name,specification,approval_number,valid_from,
            valid_until,drug_sale_class,{",".join(PROFILE_FIELDS)})
            VALUES (:t,:p,:actor,:actor,:code,:name,:category,'DRAFT',:manufacturer_name,
            
            :specification,:approval_number,:valid_from,:valid_until,:drug_sale_class,
            {",".join(":" + field for field in PROFILE_FIELDS)})
            ON CONFLICT (tenant_id,code) DO NOTHING RETURNING id""",
            p,
        )
        if not result:
            raise BusinessError("CODE_EXISTS", "商品编码已存在", 409)
        result = repo.rows(
            """INSERT INTO med.sku(tenant_id,id,created_by,updated_by,product_id,code,
            base_unit,qty_scale)
            VALUES (:t,:s,:actor,:actor,:p,:sku_code,:base_unit,:qty_scale)
            ON CONFLICT (tenant_id,code) DO NOTHING RETURNING id""",
            p,
        )
        if not result:
            raise BusinessError("CODE_EXISTS", "SKU 编码已存在", 409)
        audit(
            repo,
            ctx,
            "product",
            pid,
            "catalog.created",
            {
                **body.model_dump(mode="json"),
                "code": code,
                "sku_code": sku_code,
                "field_rules_version": rules_version,
            },
        )
        return {
            "id": str(pid),
            "sku_id": str(sid),
            "version": "1",
            "state": "DRAFT",
            "code": code,
            "sku_code": sku_code,
        }

    def create_party(self, ctx: TenantContext, body: PartyCreate, key: str) -> dict[str, Any]:
        ctx.require("catalog.write", body.entity_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            pid = uuid4()
            p = {**body.model_dump(), "t": ctx.tenant_id, "actor": ctx.principal_id, "id": pid}
            entity = repo.rows(
                """SELECT id FROM med.legal_entity WHERE id=:entity_id AND status='ACTIVE' FOR
                SHARE""",
                p,
            )
            if not entity:
                raise BusinessError("ENTITY_UNAVAILABLE", "经营主体不可用", 404)
            p["organization_id"] = resolve_party_identity(repo, ctx, body)
            result = repo.rows(
                f"""INSERT INTO med.party(tenant_id,id,entity_id,code,name,credit_code,kind,
                license_no,valid_from,valid_until,categories,created_by,updated_by,organization_id,{",".join(PARTY_PROFILE_FIELDS)})
                VALUES (:t,:id,:entity_id,:code,:name,:credit_code,:kind,:license_no,
                :valid_from,:valid_until,
                :categories,:actor,:actor,:organization_id,
                {",".join(":" + f for f in PARTY_PROFILE_FIELDS)})
                ON CONFLICT (tenant_id,entity_id,code) DO NOTHING
                RETURNING id""",
                p,
            )
            if not result:
                raise BusinessError("CODE_EXISTS", "往来单位编码已存在", 409)
            audit(
                repo,
                ctx,
                "party",
                pid,
                "party.created",
                {
                    **body.model_dump(mode="json"),
                    "organization_id": str(p["organization_id"]) if p["organization_id"] else None,
                },
            )
            return {"id": str(pid), "version": "1", "state": "DRAFT"}

        return command(
            self.uow,
            ctx,
            "catalog.party.create",
            str(body.entity_id),
            key,
            command_payload(body),
            execute,
        )

    def review(
        self,
        ctx: TenantContext,
        kind: Literal["product", "party"],
        target: UUID,
        body: Review,
        key: str,
    ) -> dict[str, Any]:
        ctx.require("catalog.review")
        table = "product" if kind == "product" else "party"
        state_column = "approval_state" if kind == "product" else "state"

        def execute(repo: SqlSession) -> dict[str, Any]:
            if kind == "product":
                from medinv.modules.catalog.application.master_config import schema_lock

                schema_lock(repo, ctx)
            rows = repo.rows(f"SELECT * FROM med.{table} WHERE id=:id FOR UPDATE", {"id": target})
            if not rows:
                raise BusinessError("NOT_FOUND", "资料不可见或不存在", 404)
            row = rows[0]
            if kind == "party":
                ctx.require("catalog.review", row["entity_id"])
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "资料已变化，请刷新", 409)
            rules_version = None
            if body.decision == "APPROVE":
                from medinv.modules.catalog.application.change_bundle import apply_pending, pending

                draft = pending(repo, kind, target)
                child_editors = repo.rows(
                    f"SELECT created_by,revision_editors FROM med.{kind}_certificate "
                    f"WHERE {kind}_id=:id AND state='DRAFT'",
                    {"id": target},
                )
                editors = set(row["revision_editors"]) | set(draft["editors"] if draft else [])
                for child in child_editors:
                    editors.update(child["revision_editors"])
                    editors.add(child["created_by"])
                if row["created_by"] == ctx.principal_id or ctx.principal_id in editors:
                    raise BusinessError(
                        "SEPARATION_REQUIRED", "建档人及本轮修订人员不能审核通过资料", 403
                    )
                apply_pending(repo, ctx, kind, row, self.uow)
                row = repo.rows(f"SELECT * FROM med.{table} WHERE id=:id", {"id": target})[0]
                today = repo.rows(
                    "SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS today", {}
                )[0]["today"]
                modern = kind == "product" and row.get("form_revision") is not None
                from medinv.modules.catalog.application.master_config import current_schema

                current_config = current_schema(repo) if kind == "product" else None
                if modern or (current_config and current_config["revision"] > 0):
                    from medinv.modules.catalog.application.master import (
                        actual_node,
                        category_node,
                        validation,
                        values_of,
                    )
                    from medinv.modules.catalog.application.master_config import current_schema

                    config = current_schema(repo)
                    if modern and row["form_revision"] != config["revision"]:
                        raise BusinessError(
                            "FORM_VERSION_CONFLICT", "请按当前配置重新校验并保存资料后审核", 409
                        )
                    node = category_node(config["payload"], actual_node(row))
                    values = values_of(repo, row)
                    issues = validation(
                        repo,
                        ctx,
                        config["payload"],
                        node["field_profile_id"],
                        values,
                        {},
                        values,
                        True,
                    )
                    if issues:
                        raise BusinessError(
                            "MASTER_VALIDATION", "；".join(i["message"] for i in issues[:10]), 422
                        )
                from medinv.modules.catalog.application.qualification_rules import settings
                from medinv.modules.catalog.application.scoped_admission import uses_legacy_dates

                qualification = settings(repo)
                legacy_dates = (
                    qualification["product_certificate_dates"]
                    if kind == "product"
                    else uses_legacy_dates(repo, target)
                )
                if (
                    not modern
                    and legacy_dates
                    and (
                        not row["valid_from"]
                        or not row["valid_until"]
                        or not row["valid_from"] <= today <= row["valid_until"]
                    )
                ):
                    raise BusinessError("QUALIFICATION_EXPIRED", "资质未生效、过期或未填写", 422)
                if (
                    kind == "product"
                    and not modern
                    and (
                        (
                            qualification["product_certificate_required"]
                            and not row["approval_number"]
                        )
                        or row["restricted_category"]
                    )
                ):
                    raise BusinessError("PRODUCT_NOT_READY", "商品资料不完整或属于未开放类别", 422)
                if kind == "product" and not modern:
                    rules_version = validate_product(repo, ctx, row, previous=row)
                if kind == "party":
                    from medinv.modules.catalog.application.scoped_admission import validate_profile

                    profiles = repo.rows(
                        "SELECT payload FROM med.party_scope_profile WHERE party_id=:p",
                        {"p": target},
                    )
                    if profiles:
                        validate_profile(repo, row, profiles[0]["payload"])
                from medinv.modules.catalog.application.change_bundle import clear

                if draft:
                    audit(
                        repo,
                        ctx,
                        kind,
                        target,
                        "catalog.change_approved",
                        {
                            "before": draft["before_snapshot"],
                            "after": draft["payload"],
                            "reason": body.reason,
                            "evidence_ref": body.evidence_ref,
                        },
                    )
                    clear(repo, kind, target)
            state = "APPROVED" if body.decision == "APPROVE" else "SUSPENDED"
            repo.execute(
                f"""UPDATE med.{table} SET {state_column}=:state,version=version+1,
              revision_editors=CASE WHEN CAST(:state AS varchar)='APPROVED'
                THEN ARRAY[]::uuid[] ELSE revision_editors END,
              reviewed_by=:actor,reviewed_at=clock_timestamp(),updated_by=:actor,updated_at=clock_timestamp()
              WHERE id=:id""",
                {"state": state, "actor": ctx.principal_id, "id": target},
            )
            audit(
                repo,
                ctx,
                kind,
                target,
                "catalog.reviewed",
                {
                    **body.model_dump(mode="json"),
                    **({"field_rules_version": rules_version} if rules_version is not None else {}),
                },
            )
            return {"id": str(target), "version": str(row["version"] + 1), "state": state}

        # Re-check entity scope before command cache lookup for party resources.
        if kind == "party":
            with self.uow.open(ctx, readonly=True) as session:
                rows = SqlSession(session).rows(
                    "SELECT entity_id FROM med.party WHERE id=:id", {"id": target}
                )
                if not rows:
                    raise BusinessError("NOT_FOUND", "资料不可见或不存在", 404)
                ctx.require("catalog.review", rows[0]["entity_id"])
        return command(
            self.uow,
            ctx,
            "catalog." + kind + ".review",
            str(target),
            key,
            body.model_dump(mode="json"),
            execute,
        )
