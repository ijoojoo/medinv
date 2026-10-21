"""Read current business conditions. An inbox acknowledgement never changes these predicates."""

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.application.party_certificates import today
from medinv.modules.catalog.application.qualification_rules import current as current_rules
from medinv.modules.catalog.application.scoped_admission import scope_problems
from medinv.modules.workspace.application.queue import QUERIES
from medinv.modules.workspace.management import EnterpriseSettings
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Source = Literal["work", "certificate", "scope", "catalog_review", "maintenance"]
SOURCES: tuple[Source, ...] = ("work", "certificate", "scope", "catalog_review", "maintenance")


def manage_allowed(ctx: TenantContext) -> bool:
    # Following up does not grant approval or stock permissions. Read-only members cannot act.
    return bool(
        ctx.permissions
        & {
            "members.manage",
            "catalog.write",
            "catalog.review",
            "quality.write",
            "quality.review",
            "purchase.write",
            "purchase.receive",
            "sales.write",
            "finance.write",
            "retail.sell",
            "counts.write",
            "transfers.write",
        }
    )


def visible_keys(ctx: TenantContext, entity: UUID) -> list[str]:
    ctx.require("tenant.read", entity)
    result = []
    if "catalog.read" in ctx.permissions:
        result.extend(["certificate:", "scope:", "catalog_review:"])
    if "quality.read" in ctx.permissions:
        result.append("maintenance:")
    result.extend(
        f"work:{k.value}:"
        for k, q in QUERIES.items()
        if q.permission in ctx.permissions
        and (not q.additional_permission or q.additional_permission in ctx.permissions)
    )
    return result


def require_source(ctx: TenantContext, entity: UUID, source: str, key: str = "") -> None:
    prefixes = visible_keys(ctx, entity)
    if not any(
        (key or source + ":").startswith(p) or (not key and p.startswith(source + ":"))
        for p in prefixes
    ):
        raise BusinessError("FORBIDDEN", "没有读取此提醒来源的权限", 403)
    if key and not key.startswith(source + ":"):
        raise BusinessError("MESSAGE_INVALID", "消息来源不匹配", 422)


def configuration(repo: SqlSession, entity: UUID) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT payload FROM med.management_record WHERE entity_id=:e AND kind='settings'",
        {"e": entity},
    )
    return dict(EnterpriseSettings.model_validate(rows[0]["payload"] if rows else {}).model_dump())


def decorate(row: dict[str, Any], source: str) -> dict[str, Any]:
    row = dict(jsonable_encoder(row))
    revision = hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:24]
    return {
        **row,
        "source": source,
        "source_revision": revision,
        "message_id": row.get("message_id") or row["source_key"] + ":" + revision,
    }


def scopes(repo: SqlSession, entity: UUID, offset: int, key: str) -> dict[str, Any]:
    # Bound each request even for large tenants. Offset is a scan cursor for this source only.
    candidates = repo.rows(
        """SELECT p.id AS party_id,p.name,f.version,s.value AS scope,
        ch.value AS channel,'scope:'||p.id||':'||(s.value->>'id')||':'||ch.value AS source_key
        FROM med.party_scope_profile f JOIN med.party p
          ON (p.tenant_id,p.id)=(f.tenant_id,f.party_id)
        CROSS JOIN LATERAL jsonb_array_elements(f.payload->'scopes') s
        CROSS JOIN LATERAL jsonb_array_elements_text(s.value->'channels') ch
        WHERE f.entity_id=:e AND (s.value->>'enabled')::boolean
          AND (:key='' OR 'scope:'||p.id||':'||(s.value->>'id')||':'||ch.value=:key)
        ORDER BY p.id,s.value->>'id',ch.value LIMIT 251 OFFSET :o""",
        {"e": entity, "key": key, "o": offset},
    )
    rules = current_rules(repo)
    config, day = rules["payload"], str(today(repo))
    certs: dict[str, dict[str, Any]] = {}
    if candidates:
        certs = {
            str(c["id"]): c
            for c in repo.rows(
                "SELECT * FROM med.party_certificate WHERE party_id=ANY(:ids)",
                {"ids": list({r["party_id"] for r in candidates})},
            )
        }
    items = []
    consumed = 0
    for r in candidates[:250]:
        consumed += 1
        problems = scope_problems(config, r["scope"], certs, r["channel"], day)
        if problems:
            scope = r["scope"]
            items.append(
                decorate(
                    {
                        "source_key": r["source_key"],
                        "target_id": r["party_id"],
                        "page": "parties",
                        "tab": "scopes",
                        "title": r["name"] + " · " + scope["name"],
                        "due": None,
                        "description": "；".join(problems),
                        "scope_codes": scope["codes"],
                        "channel": r["channel"],
                        "enforcement": config["enforcement"],
                        "impact": config["impact"],
                        "profile_version": str(r["version"]),
                        "rules_version": str(rules["version"]),
                    },
                    "scope",
                )
            )
        if len(items) == 50:
            break
    return {
        "items": items,
        "has_more": len(candidates) > consumed,
        "next_offset": offset + consumed,
    }


def current_items(
    repo: SqlSession,
    ctx: TenantContext,
    entity: UUID,
    source: str,
    offset: int = 0,
    key: str = "",
    *,
    verify: bool = False,
) -> dict[str, Any]:
    require_source(ctx, entity, source, key)
    config = configuration(repo, entity)
    enabled = config["reminders_enabled"] and (source != "work" or config["work_queue_enabled"])
    enabled = enabled and config.get(source + "_reminders_enabled", True)
    if not enabled and not verify:
        return {"items": [], "has_more": False, "next_offset": offset, "disabled": True}
    if source == "scope":
        return {**scopes(repo, entity, offset, key), "disabled": False}
    params: dict[str, Any] = {"e": entity, "o": offset, "key": key}
    if source == "work":
        union = " UNION ALL ".join(
            f"SELECT 'work:{k.value}:'||task.item_id AS source_key,"
            f"'{q.label}'||' · '||number AS title,"
            f"document_time AS due,'{q.page}' AS page,target_id,'{k.value}' AS kind,task.item_id,"
            "number,related_number,party_name,warehouse_name,document_time,reservation_expired "
            f"FROM ({q.sql}) task"
            for k, q in QUERIES.items()
            if f"work:{k.value}:" in visible_keys(ctx, entity)
        )
        sql = union
        order = "reservation_expired DESC,due,source_key"
        repo.execute("SET LOCAL plan_cache_mode = force_custom_plan", {})
    elif source == "certificate":
        params["days"] = config["certificate_notice_days"]
        sql = """SELECT 'certificate:'||c.id AS source_key,
            'certificate:'||c.id||':'||c.version AS message_id,
            p.name||' · '||c.number AS title,c.valid_until AS due,'parties' AS page,
            p.id AS target_id,'certificates' AS tab,c.version,c.state,c.certificate_type
          FROM med.party_certificate c JOIN med.party p
            ON (p.tenant_id,p.id)=(c.tenant_id,c.party_id)
          WHERE p.entity_id=:e AND c.state<>'INACTIVE' AND c.validity_mode='FIXED'
            AND c.valid_until <= (now() AT TIME ZONE 'Asia/Shanghai')::date + :days
          UNION ALL
          SELECT 'certificate:product:'||p.id||':'||c.id AS source_key,
            'certificate:product:'||p.id||':'||c.id||':'||c.version AS message_id,
            p.name||' · '||c.number AS title,c.valid_until AS due,'products' AS page,
            p.id AS target_id,'certificates' AS tab,c.version,c.state,c.certificate_type
          FROM (SELECT tenant_id,product_id,id AS certificate_id FROM med.product_certificate
                UNION SELECT tenant_id,product_id,certificate_id
                FROM med.product_certificate_link) l
          JOIN med.product_certificate c ON (c.tenant_id,c.id)=(l.tenant_id,l.certificate_id)
          JOIN med.product p ON (p.tenant_id,p.id)=(l.tenant_id,l.product_id)
          WHERE c.state<>'INACTIVE' AND c.validity_mode='FIXED'
            AND c.valid_until <= (now() AT TIME ZONE 'Asia/Shanghai')::date + :days"""
        order = "due,source_key"
    elif source == "catalog_review":
        sql = " UNION ALL ".join(
            f"""SELECT 'catalog_review:{kind}:'||p.id AS source_key,p.name||' · '||
                CASE WHEN b.target_id IS NULL THEN '首次资料待审核'
                ELSE '资料变更待审核' END AS title,
                coalesce(b.updated_at,p.updated_at) AS due,'{page}' AS page,p.id AS target_id,
                'review' AS tab,p.version,p.{state} AS state,
                coalesce(b.updated_at,p.updated_at) AS revision_time
                FROM med.{kind} p LEFT JOIN med.catalog_change_bundle b ON
                  (b.tenant_id,b.kind,b.target_id)=(p.tenant_id,'{kind}',p.id)
                WHERE (p.{state}='DRAFT'
                  OR b.target_id IS NOT NULL)
                {"AND p.entity_id=:e" if kind == "party" else ""}"""
            for kind, page, state in (
                ("party", "parties", "state"),
                ("product", "products", "approval_state"),
            )
        )
        order = "due,source_key"
    else:
        params["days"] = config["maintenance_notice_days"]
        sql = """WITH latest AS (
            SELECT DISTINCT ON (payload->>'product_id',payload->>'lot_no') *
            FROM med.management_record
            WHERE entity_id=:e AND kind='maintenance' AND payload->>'state'='COMPLETED'
            ORDER BY payload->>'product_id',payload->>'lot_no',
            payload->>'checked_on' DESC,updated_at DESC,id)
          SELECT 'maintenance:'||r.id AS source_key,
            'maintenance:'||r.id||':'||r.version AS message_id,
            p.name||' · '||(r.payload->>'lot_no') AS title,r.payload->>'next_due' AS due,
            'quality-ledgers' AS page,r.id AS target_id,'' AS tab,r.version
          FROM latest r JOIN med.product p ON
            (p.tenant_id,p.id)=(r.tenant_id,(r.payload->>'product_id')::uuid)
          WHERE (r.payload->>'next_due')::date <=
            (now() AT TIME ZONE 'Asia/Shanghai')::date + :days"""
        order = "due,source_key"
    rows = repo.rows(
        f"SELECT * FROM ({sql}) alerts WHERE (:key='' OR source_key=:key) "
        f"ORDER BY {order} LIMIT 51 OFFSET :o",
        params,
    )
    items = []
    for row in rows[:50]:
        if source == "work":
            row["message_id"] = row["source_key"]
            row["work_item"] = {**row, "entity_id": entity}
        items.append(decorate(row, source))
    return {
        "items": items,
        "has_more": len(rows) > 50,
        "next_offset": offset + len(items),
        "disabled": False,
    }
