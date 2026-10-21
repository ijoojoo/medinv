"""Tenant-scoped follow-up cases with verified closure and an immutable audit trail."""

import json
from datetime import date
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import Field, StrictStr

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.workspace.management import Version, require_entity
from medinv.modules.workspace.reminder_sources import (
    Source,
    current_items,
    manage_allowed,
    require_source,
    visible_keys,
)
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class ReminderAction(DTO):
    entity_id: UUID
    source: Source
    source_key: Annotated[StrictStr, Field(min_length=1, max_length=180)]
    expected_version: Version
    observed_revision: Annotated[StrictStr, Field(pattern=r"^([a-f0-9]{24})?$")] = ""
    action: Literal["CLAIM", "ASSIGN", "NOTE", "CLOSE"]
    assigned_staff_id: UUID | None = None
    due_on: date | None = None
    reason: Text


def present(
    ctx: TenantContext,
    snapshot: dict[str, Any],
    case: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    active = current is not None
    state = "NEW"
    if case:
        state = (
            ("REOPEN_REQUIRED" if active else "CLOSED")
            if case["state"] == "CLOSED"
            else ("PROCESSING" if active else "RESOLVED")
        )
    return dict(
        jsonable_encoder(
            {
                **(current or snapshot),
                "case_id": case["id"] if case else None,
                "case_version": str(case["version"]) if case else "0",
                "process_state": state,
                "condition_present": active,
                "condition_changed": bool(
                    case
                    and current
                    and case["snapshot"]["source_revision"] != current["source_revision"]
                ),
                "claimed_by": case["claimed_by"] if case else None,
                "claimed_by_me": bool(case and case["claimed_by"] == ctx.principal_id),
                "assigned_staff_id": case["assigned_staff_id"] if case else None,
                "assigned_name": case["assigned_name"] if case else "",
                "due_on": case["due_on"] if case else None,
                "closed_at": case["closed_at"] if case else None,
                "can_manage": manage_allowed(ctx),
            }
        )
    )


class Reminders:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(
        self, ctx: TenantContext, entity: UUID, source: str, offset: int = 0, target: str = ""
    ) -> dict[str, Any]:
        ctx.require("tenant.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            data = current_items(repo, ctx, entity, source, offset)
            rows = data["items"]
            # Compatibility for older internal callers using versioned message IDs.
            if target:
                key = target
                if source in {"certificate", "maintenance"}:
                    key = target.rsplit(":", 1)[0]
                data = current_items(repo, ctx, entity, source, 0, key)
                rows = [r for r in data["items"] if r["message_id"] == target]
            cases = {
                r["source_key"]: r
                for r in repo.rows(
                    "SELECT * FROM med.reminder_case WHERE entity_id=:e AND source_key=ANY(:keys)",
                    {"e": entity, "keys": [r["source_key"] for r in rows]},
                )
            }
            markers = repo.rows(
                """SELECT payload->>'message_id' AS id FROM med.management_record
                WHERE entity_id=:e AND kind='notification_read' AND created_by=:a
                  AND record_key=ANY(:keys)""",
                {
                    "e": entity,
                    "a": ctx.principal_id,
                    "keys": [str(ctx.principal_id) + ":" + r["message_id"] for r in rows],
                },
            )
            read = {r["id"] for r in markers}
            return {
                **data,
                "items": [
                    {
                        **present(ctx, r, cases.get(r["source_key"]), r),
                        "read": r["message_id"] in read,
                    }
                    for r in rows
                ],
            }

    def cases(
        self,
        ctx: TenantContext,
        entity: UUID,
        state: str,
        mine: bool,
        offset: int,
        source: str = "",
    ) -> dict[str, Any]:
        prefixes = visible_keys(ctx, entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            rows = repo.rows(
                """SELECT * FROM med.reminder_case c WHERE entity_id=:e
                AND state=:state AND (NOT :mine OR claimed_by=:a)
                AND (:source='' OR source=:source)
                AND EXISTS(SELECT 1 FROM unnest(CAST(:prefixes AS text[])) prefix
                  WHERE starts_with(c.source_key,prefix))
                ORDER BY updated_at DESC,id LIMIT 51 OFFSET :o""",
                {
                    "e": entity,
                    "state": state,
                    "mine": mine,
                    "a": ctx.principal_id,
                    "source": source,
                    "prefixes": prefixes,
                    "o": offset,
                },
            )
            items = []
            for row in rows[:50]:
                active = current_items(
                    repo, ctx, entity, row["source"], key=row["source_key"], verify=True
                )["items"]
                items.append(present(ctx, row["snapshot"], row, active[0] if active else None))
            return {
                "items": items,
                "has_more": len(rows) > 50,
                "disabled": False,
                "next_offset": offset + min(50, len(rows)),
            }

    def detail(self, ctx: TenantContext, entity: UUID, target: UUID) -> dict[str, Any]:
        ctx.require("tenant.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            rows = repo.rows(
                "SELECT * FROM med.reminder_case WHERE id=:id AND entity_id=:e",
                {"id": target, "e": entity},
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "处理事项不可见或不存在", 404)
            case = rows[0]
            active = current_items(
                repo, ctx, entity, case["source"], key=case["source_key"], verify=True
            )["items"]
            history = repo.rows(
                """SELECT created_at,created_by,action,reason FROM med.audit_event
                WHERE resource_type='reminder_case' AND resource_id=:id
                ORDER BY created_at DESC,id DESC LIMIT 50""",
                {"id": target},
            )
            return dict(
                jsonable_encoder(
                    {
                        "item": present(ctx, case["snapshot"], case, active[0] if active else None),
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in history],
                    }
                )
            )

    def staff(self, ctx: TenantContext, entity: UUID, search: str) -> dict[str, Any]:
        ctx.require("tenant.read", entity)
        if not manage_allowed(ctx):
            raise BusinessError("FORBIDDEN", "没有分派提醒事项的权限", 403)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            rows = repo.rows(
                """SELECT id,payload->>'code' AS code,payload->>'name' AS name,
                payload->>'job' AS job FROM med.management_record
                WHERE entity_id=:e AND kind='staff'
                AND payload->>'status'='ACTIVE' AND (:q='' OR
                  strpos(lower((payload->>'code')||' '||(payload->>'name')),lower(:q))>0)
                ORDER BY payload->>'code',id LIMIT 51""",
                {"e": entity, "q": search},
            )
            return dict(jsonable_encoder({"items": rows[:50], "has_more": len(rows) > 50}))

    def act(self, ctx: TenantContext, body: ReminderAction, key: str) -> dict[str, Any]:
        require_source(ctx, body.entity_id, body.source, body.source_key)
        if not manage_allowed(ctx):
            raise BusinessError("FORBIDDEN", "只读成员不能处理提醒事项", 403)

        def run(repo: SqlSession) -> dict[str, Any]:
            require_entity(repo, body.entity_id)
            repo.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))",
                {"lock": f"reminder:{ctx.tenant_id}:{body.entity_id}:{body.source_key}"},
            )
            found = repo.rows(
                "SELECT * FROM med.reminder_case WHERE entity_id=:e AND source_key=:key FOR UPDATE",
                {"e": body.entity_id, "key": body.source_key},
            )
            before = found[0] if found else None
            if body.expected_version != str(before["version"] if before else 0):
                raise BusinessError("VERSION_CONFLICT", "处理记录已变化，请刷新后操作", 409)
            active = current_items(
                repo, ctx, body.entity_id, body.source, key=body.source_key, verify=True
            )["items"]
            current = active[0] if active else None
            if current and body.observed_revision != current["source_revision"]:
                raise BusinessError(
                    "REMINDER_CHANGED", "原资料或提醒条件已变化，请刷新核对后操作", 409
                )
            if not before and not current:
                raise BusinessError("NOT_FOUND", "此提醒已消除或不可见，请刷新", 404)
            if body.action == "CLOSE" and current:
                raise BusinessError(
                    "REMINDER_UNRESOLVED", "原事项仍触发提醒，请先进入原资料或原单完成处理", 409
                )
            if before and before["state"] == "CLOSED" and (not current or body.action != "CLAIM"):
                raise BusinessError("REMINDER_CLOSED", "此事项已关闭；再次触发时请重新认领", 409)
            if (
                body.action == "CLAIM"
                and before
                and before["state"] != "CLOSED"
                and before["claimed_by"] not in (None, ctx.principal_id)
            ):
                raise BusinessError(
                    "REMINDER_CLAIMED", "此事项已被其他成员认领，请通过分派负责人协作处理", 409
                )
            snapshot = current or (before or {})["snapshot"]
            values = {
                "id": before["id"] if before else uuid4(),
                "claimed": before["claimed_by"] if before else None,
                "staff": before["assigned_staff_id"] if before else None,
                "name": before["assigned_name"] if before else "",
                "due": before["due_on"] if before else None,
            }
            if body.action == "CLAIM":
                values["claimed"] = ctx.principal_id
            if body.action == "ASSIGN":
                staff = repo.rows(
                    """SELECT payload FROM med.management_record
                    WHERE id=:id AND entity_id=:e AND kind='staff' AND payload->>'status'='ACTIVE'
                    FOR SHARE""",
                    {"id": body.assigned_staff_id, "e": body.entity_id},
                )
                if not staff:
                    raise BusinessError("NOT_FOUND", "负责人须选择本经营主体的在职职员", 404)
                values.update(
                    staff=body.assigned_staff_id, name=staff[0]["payload"]["name"], due=body.due_on
                )
            closed = body.action == "CLOSE"
            result = repo.rows(
                """INSERT INTO med.reminder_case(tenant_id,id,entity_id,source,source_key,
                snapshot,state,claimed_by,assigned_staff_id,assigned_name,due_on,closed_at,
                closed_by,created_by,updated_by)
                VALUES(:t,:id,:e,:source,:key,CAST(:snapshot AS jsonb),
                  :state,:claimed,:staff,:name,:due,
                  CASE WHEN :closed THEN now() END,
                  CASE WHEN :closed THEN CAST(:a AS uuid) END,:a,:a)
                ON CONFLICT(tenant_id,entity_id,source_key) DO UPDATE
                  SET snapshot=EXCLUDED.snapshot,
                  state=EXCLUDED.state,claimed_by=EXCLUDED.claimed_by,assigned_staff_id=EXCLUDED.assigned_staff_id,
                  assigned_name=EXCLUDED.assigned_name,due_on=EXCLUDED.due_on,closed_at=EXCLUDED.closed_at,
                  closed_by=EXCLUDED.closed_by,version=med.reminder_case.version+1,updated_at=now(),updated_by=:a
                RETURNING *""",
                {
                    **values,
                    "t": ctx.tenant_id,
                    "e": body.entity_id,
                    "source": body.source,
                    "key": body.source_key,
                    "snapshot": json.dumps(snapshot, ensure_ascii=False),
                    "state": "CLOSED" if closed else "PROCESSING",
                    "closed": closed,
                    "a": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "reminder_case",
                result["id"],
                "reminder." + body.action.lower(),
                {
                    "reason": body.reason,
                    "source_key": body.source_key,
                    "condition_present": bool(current),
                    "before": jsonable_encoder(before),
                    "after": jsonable_encoder(result),
                },
            )
            return present(ctx, snapshot, result, current)

        return command(
            self.uow,
            ctx,
            "reminder.action",
            sha256(f"{body.entity_id}:{body.source_key}".encode()).hexdigest(),
            key,
            body.model_dump(mode="json"),
            run,
        )
