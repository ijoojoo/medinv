"""Explicitly reviewed scope declarations; never interpret licence free text."""

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.admission import (
    AdmissionPreview,
    AdmissionReview,
    AdmissionSave,
    Channel,
)
from medinv.modules.catalog.application.party_certificates import today
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

EMPTY = {"enabled": False, "own_scopes": [], "party_scopes": []}
FIELDS = {"DRUG": "drug_business_scope", "DEVICE": "device_business_scope"}


def entity_lock(repo: SqlSession, entity: UUID, *, write: bool = False) -> None:
    rows = repo.rows(
        "SELECT status FROM med.legal_entity WHERE id=:e FOR " + ("UPDATE" if write else "SHARE"),
        {"e": entity},
    )
    if not rows or rows[0]["status"] != "ACTIVE":
        raise BusinessError("ENTITY_UNAVAILABLE", "主体不存在或已停用", 404)


def policy(repo: SqlSession, entity: UUID, channel: str) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.admission_policy WHERE entity_id=:e AND channel=:c",
        {"e": entity, "c": channel},
    )
    return (
        rows[0]
        if rows
        else {
            "id": None,
            "entity_id": entity,
            "channel": channel,
            "version": 0,
            "active_version": 0,
            "payload": EMPTY,
            "active_payload": EMPTY,
            "editors": [],
            "reviewed_by": None,
            "reviewed_at": None,
        }
    )


def present(row: dict[str, Any]) -> dict[str, Any]:
    return dict(
        jsonable_encoder(
            {
                **row,
                "version": str(row["version"]),
                "active_version": str(row["active_version"]),
                "has_draft": row["version"] != row["active_version"],
            }
        )
    )


def dictionaries(repo: SqlSession) -> dict[str, set[str]]:
    rows = repo.rows(
        "SELECT key,items FROM med.catalog_dictionary WHERE key=ANY(:keys) ORDER BY key FOR SHARE",
        {"keys": list(FIELDS.values())},
    )
    return {r["key"]: {i["value"] for i in r["items"] if i["active"]} for r in rows}


def certificate_problem(cert: dict[str, Any], grant: dict[str, Any], channel: str, day: str) -> str:
    if str(cert["version"]) != grant["certificate_version"]:
        return "关联证照版本已变化，请重新核验范围"
    if cert["state"] != "VERIFIED":
        return "关联证照尚未核验或已停用"
    if ("SUPPLY" if channel == "PURCHASE" else "PURCHASE") not in cert["business_uses"]:
        return "关联证照的业务用途不匹配"
    if cert["validity_mode"] == "FIXED":
        if not str(cert["valid_from"]) <= day <= str(cert["valid_until"]):
            return "关联证照未生效或已过期"
    elif cert["validity_mode"] != "LONG_TERM":
        return "关联证照有效期未明确，需人工核验"
    return ""


def evaluate(
    repo: SqlSession,
    entity: UUID,
    channel: str,
    party: dict[str, Any],
    products: list[dict[str, Any]],
    *,
    row: dict[str, Any] | None = None,
    draft: bool = False,
) -> dict[str, Any]:
    """Caller holds entity, party and product locks through the business commit."""
    from medinv.modules.catalog.application.scoped_admission import evaluate as evaluate_scoped

    scoped = evaluate_scoped(repo, entity, channel, party, products, own_policy=row, draft=draft)
    if scoped is not None:
        return scoped
    row = row or policy(repo, entity, channel)
    payload = row["payload" if draft else "active_payload"]
    result: dict[str, Any] = {
        "enabled": payload["enabled"],
        "source": "DRAFT" if draft else "ACTIVE",
        "policy_id": str(row["id"]) if row["id"] else None,
        "policy_version": str(row["version"] if draft else row["active_version"]),
        "channel": channel,
        "issues": [],
        "items": [],
    }
    if not payload["enabled"]:
        return {**result, "passed": True, "message": "结构化准入未启用；原有业务校验继续执行"}
    day = str(today(repo))
    values = dictionaries(repo)
    grants = [g for g in payload["party_scopes"] if g["party_id"] == str(party["id"])]
    cert_ids = sorted({UUID(g["certificate_id"]) for g in grants})
    certs = (
        {
            str(r["id"]): r
            for r in repo.rows(
                "SELECT * FROM med.party_certificate WHERE party_id=:p AND id=ANY(:ids) "
                "ORDER BY id FOR SHARE",
                {"p": party["id"], "ids": cert_ids},
            )
        }
        if cert_ids
        else {}
    )
    required_kind = "SUPPLIER" if channel == "PURCHASE" else "CUSTOMER"
    party_ok = (
        party["entity_id"] == entity
        and party["kind"] in {required_kind, "BOTH"}
        and party["state"] == "APPROVED"
        and str(party["valid_from"]) <= day <= str(party["valid_until"])
    )
    for product in products:
        category = product["category"]
        value = product.get(FIELDS.get(category, ""), "")
        problems = []
        if not party_ok:
            problems.append("往来单位角色、审核状态或有效期不符合本业务")
        if category not in FIELDS:
            problems.append("当前结构化规则仅覆盖普通药品、医疗器械，其他类别未配置")
        elif not value:
            problems.append("商品未填写结构化经营范围类别")
        elif value not in values.get(FIELDS[category], set()):
            problems.append("商品范围不在当前启用的范围字典中")
        own = next(
            (g for g in payload["own_scopes"] if (g["category"], g["value"]) == (category, value)),
            None,
        )
        other = next((g for g in grants if (g["category"], g["value"]) == (category, value)), None)
        for label, grant in [("本企业", own), ("往来单位", other)]:
            if not grant:
                problems.append(f"{label}缺少该范围的核验记录")
            elif not grant["valid_from"] <= day <= grant["valid_until"]:
                problems.append(f"{label}范围核验未生效或已过期")
        if other:
            cert = certs.get(other["certificate_id"])
            issue = (
                certificate_problem(cert, other, channel, day)
                if cert
                else "关联证照不可见或不属于此单位"
            )
            if issue:
                problems.append(issue)
        item = {
            "sku_id": str(product["id"]),
            "name": product["name"],
            "scope": value,
            "passed": not problems,
            "reasons": problems,
        }
        result["items"].append(item)
        result["issues"].extend(
            f"{product['name']}（{value or '范围待填写'}）：{p}" for p in problems
        )
        if not problems:
            product["admission"] = {
                "policy_id": result["policy_id"],
                "policy_version": result["policy_version"],
                "channel": channel,
                "checked_on": day,
                "own_scope": own,
                "party_scope": other,
            }
    return {
        **result,
        "passed": not result["issues"],
        "message": "仅检查结构化范围，其他业务校验仍须通过",
    }


def enforce(
    repo: SqlSession,
    entity: UUID,
    channel: str,
    party: dict[str, Any],
    products: list[dict[str, Any]],
) -> None:
    result = evaluate(repo, entity, channel, party, products)
    if not result["passed"]:
        suffix = (
            f"；另有 {len(result['issues']) - 3} 项，请使用准入试算查看"
            if len(result["issues"]) > 3
            else ""
        )
        raise BusinessError("ADMISSION_BLOCKED", "；".join(result["issues"][:3]) + suffix, 422)


def reserve_admission(repo: SqlSession, entity: UUID, source: UUID, sku_ids: list[UUID]) -> bool:
    # Public inventory reservation must also recheck a real wholesale source.
    from medinv.modules.catalog.application.scoped_admission import profile, uses_legacy_dates

    legacy_enabled = policy(repo, entity, "WHOLESALE")["active_payload"]["enabled"]
    orders = repo.rows(
        "SELECT customer_id FROM med.sales_order WHERE id=:id AND entity_id=:e",
        {"id": source, "e": entity},
    )
    if not orders:
        if not legacy_enabled:
            return False
        raise BusinessError(
            "ADMISSION_SOURCE_REQUIRED", "启用批发准入后须使用可见的销售订单预占", 422
        )
    parties = repo.rows(
        "SELECT * FROM med.party WHERE id=:id AND entity_id=:e FOR SHARE",
        {"id": orders[0]["customer_id"], "e": entity},
    )
    if not parties:
        raise BusinessError("NOT_FOUND", "客户不可见", 404)
    if parties[0]["state"] != "APPROVED" or parties[0]["kind"] not in {"CUSTOMER", "BOTH"}:
        raise BusinessError("CUSTOMER_INELIGIBLE", "客户未审核、已停用或供销身份不符", 422)
    scoped = profile(repo, parties[0]["id"]) is not None
    customer = parties[0]
    if uses_legacy_dates(repo, customer["id"]) and (
        not customer["valid_from"]
        or not customer["valid_until"]
        or not customer["valid_from"] <= today(repo) <= customer["valid_until"]
    ):
        raise BusinessError("CUSTOMER_INELIGIBLE", "客户旧资料资质未填写或已失效", 422)
    if not legacy_enabled and not scoped:
        return False
    rows = repo.rows(
        """SELECT s.id,p.name,p.category,p.drug_business_scope,p.device_business_scope
        FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
        WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p""",
        {"ids": sorted(set(sku_ids))},
    )
    if len(rows) != len(set(sku_ids)):
        raise BusinessError("NOT_FOUND", "商品不可见", 404)
    enforce(repo, entity, "WHOLESALE", parties[0], rows)
    return scoped


class AdmissionPolicies:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def products(self, ctx: TenantContext, entity: UUID, search: str) -> dict[str, Any]:
        ctx.require("catalog.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT s.id,s.code,p.name,p.category,
                p.drug_business_scope,p.device_business_scope
                FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                WHERE s.code ILIKE :q OR p.name ILIKE :q ORDER BY s.code,s.id LIMIT 51""",
                {"q": "%" + search + "%"},
            )
            return {"items": jsonable_encoder(rows[:50]), "has_more": len(rows) > 50}

    def detail(self, ctx: TenantContext, entity: UUID, channel: Channel) -> dict[str, Any]:
        ctx.require("catalog.read", entity)
        with self.uow.open(ctx) as session:
            repo = SqlSession(session)
            entity_lock(repo, entity)
            row = policy(repo, entity, channel)
            history = (
                repo.rows(
                    """SELECT action,created_at,created_by,reason FROM med.audit_event
                WHERE resource_type='admission_policy' AND resource_id=:id
                ORDER BY created_at DESC,id LIMIT 30""",
                    {"id": row["id"]},
                )
                if row["id"]
                else []
            )
            return {
                **present(row),
                "history": jsonable_encoder(
                    [{**h, "detail": json.loads(h["reason"])} for h in history]
                ),
            }

    @staticmethod
    def validate(
        repo: SqlSession, entity: UUID, channel: str, payload: dict[str, Any], *, reviewing: bool
    ) -> None:
        ids = sorted({UUID(g["party_id"]) for g in payload["party_scopes"]})
        parties = (
            {
                str(p["id"]): p
                for p in repo.rows(
                    "SELECT * FROM med.party WHERE entity_id=:e AND id=ANY(:ids) "
                    "ORDER BY id FOR SHARE",
                    {"e": entity, "ids": ids},
                )
            }
            if ids
            else {}
        )
        values = dictionaries(repo)
        day = str(today(repo))
        for grant in payload["own_scopes"] + payload["party_scopes"]:
            if (
                payload["enabled"]
                and not grant.get("codes")
                and grant["value"] not in values.get(FIELDS[grant["category"]], set())
            ):
                raise BusinessError(
                    "ADMISSION_SCOPE_INVALID", "范围类别须选用当前启用的统一字典值", 422
                )
            if "party_id" not in grant:
                continue
            party = parties.get(grant["party_id"])
            if not party:
                raise BusinessError("NOT_FOUND", "往来单位不属于当前主体或不可见", 404)
            if party["kind"] not in {"BOTH", "SUPPLIER" if channel == "PURCHASE" else "CUSTOMER"}:
                raise BusinessError("ADMISSION_ROLE_INVALID", "往来单位角色与准入业务不匹配", 422)
        cert_ids = sorted({UUID(g["certificate_id"]) for g in payload["party_scopes"]})
        certs = (
            {
                str(c["id"]): c
                for c in repo.rows(
                    "SELECT * FROM med.party_certificate WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
                    {"ids": cert_ids},
                )
            }
            if cert_ids
            else {}
        )
        for grant in payload["party_scopes"]:
            from medinv.modules.catalog.application.scoped_admission import profile

            if profile(repo, UUID(grant["party_id"])) is not None:
                continue
            cert = certs.get(grant["certificate_id"])
            if not cert or str(cert["party_id"]) != grant["party_id"]:
                raise BusinessError("NOT_FOUND", "证照不可见或不属于配置的往来单位", 404)
            issue = certificate_problem(cert, grant, channel, day)
            if reviewing and payload["enabled"] and issue:
                raise BusinessError("ADMISSION_EVIDENCE_INVALID", issue, 422)

    def save(
        self, ctx: TenantContext, entity: UUID, channel: Channel, body: AdmissionSave, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.configure", entity)

        def run(repo: SqlSession) -> dict[str, Any]:
            entity_lock(repo, entity, write=True)
            before = policy(repo, entity, channel)
            if str(before["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "准入配置已变化，请重新加载", 409)
            payload = body.payload.model_dump(mode="json")
            self.validate(repo, entity, channel, payload, reviewing=False)
            if before["id"] and payload == before["payload"]:
                raise BusinessError("UNCHANGED", "准入配置没有变化", 422)
            editors = set(
                before["editors"] if before["version"] != before["active_version"] else []
            )
            editors.add(str(ctx.principal_id))
            row = repo.rows(
                """INSERT INTO med.admission_policy
                (tenant_id,id,entity_id,channel,payload,editors,created_by,updated_by)
                VALUES (:t,:id,:e,:c,CAST(:payload AS jsonb),CAST(:editors AS jsonb),:a,:a)
                ON CONFLICT(tenant_id,entity_id,channel) DO UPDATE SET payload=EXCLUDED.payload,
                editors=EXCLUDED.editors,version=admission_policy.version+1,updated_by=:a,updated_at=clock_timestamp()
                RETURNING *""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "e": entity,
                    "c": channel,
                    "payload": json.dumps(payload),
                    "editors": json.dumps(sorted(editors)),
                    "a": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "admission_policy",
                row["id"],
                "catalog.admission_saved",
                {"reason": body.reason, "before": present(before), "after": present(row)},
            )
            return present(row)

        return command(
            self.uow,
            ctx,
            "catalog.admission.save",
            f"{entity}:{channel}",
            key,
            body.model_dump(mode="json"),
            run,
        )

    def review(
        self, ctx: TenantContext, entity: UUID, channel: Channel, body: AdmissionReview, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.review", entity)

        def run(repo: SqlSession) -> dict[str, Any]:
            entity_lock(repo, entity, write=True)
            before = policy(repo, entity, channel)
            if str(before["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "准入配置已变化，请重新加载", 409)
            if before["version"] == before["active_version"]:
                raise BusinessError("NO_DRAFT", "没有待复核的准入配置", 422)
            if str(ctx.principal_id) in before["editors"]:
                raise BusinessError(
                    "INDEPENDENT_REVIEW_REQUIRED", "请由未参与本次配置修改的人员独立复核", 422
                )
            self.validate(repo, entity, channel, before["payload"], reviewing=True)
            row = repo.rows(
                """UPDATE med.admission_policy SET active_payload=payload,version=version+1,
                active_version=version+1,reviewed_by=:a,reviewed_at=clock_timestamp(),
                updated_by=:a,updated_at=clock_timestamp() WHERE id=:id RETURNING *""",
                {"id": before["id"], "a": ctx.principal_id},
            )[0]
            audit(
                repo,
                ctx,
                "admission_policy",
                row["id"],
                "catalog.admission_published",
                {"reason": body.reason, "before": present(before), "after": present(row)},
            )
            return present(row)

        return command(
            self.uow,
            ctx,
            "catalog.admission.review",
            f"{entity}:{channel}",
            key,
            body.model_dump(mode="json"),
            run,
        )

    def preview(
        self, ctx: TenantContext, entity: UUID, channel: Channel, body: AdmissionPreview
    ) -> dict[str, Any]:
        ctx.require("catalog.read", entity)
        with self.uow.open(ctx) as session:
            repo = SqlSession(session)
            entity_lock(repo, entity)
            parties = repo.rows(
                "SELECT * FROM med.party WHERE id=:id AND entity_id=:e FOR SHARE",
                {"id": body.party_id, "e": entity},
            )
            if not parties:
                raise BusinessError("NOT_FOUND", "往来单位不可见或不属于当前主体", 404)
            products = repo.rows(
                """SELECT s.id,p.name,p.category,p.drug_business_scope,p.device_business_scope
                FROM med.sku s JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p""",
                {"ids": sorted(set(body.sku_ids))},
            )
            if len(products) != len(set(body.sku_ids)):
                raise BusinessError("NOT_FOUND", "商品不可见", 404)
            return evaluate(
                repo, entity, channel, parties[0], products, draft=body.source == "DRAFT"
            )
