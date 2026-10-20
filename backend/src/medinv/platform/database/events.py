import json
from typing import Any
from uuid import UUID, uuid4

from medinv.platform.database.sql import SqlSession
from medinv.platform.tenancy.context import TenantContext


def audit(
    repo: SqlSession,
    ctx: TenantContext,
    kind: str,
    target: UUID,
    action: str,
    detail: dict[str, Any],
) -> None:
    repo.execute(
        """INSERT INTO med.audit_event
        (tenant_id,id,created_by,actor_subject,action,resource_type,resource_id,
        request_id,reason)
        VALUES (:t,:id,:actor,CAST(:actor AS text),:action,:kind,:target,:request,
        :reason)""",
        {
            "t": ctx.tenant_id,
            "id": uuid4(),
            "actor": ctx.principal_id,
            "action": action,
            "kind": kind,
            "target": target,
            "request": ctx.request_id,
            "reason": json.dumps(detail, ensure_ascii=False),
        },
    )


def event(
    repo: SqlSession,
    ctx: TenantContext,
    event_id: UUID,
    kind: str,
    target: UUID,
    version: int,
    action: str,
    payload: dict[str, Any],
) -> None:
    repo.execute(
        """INSERT INTO med.outbox_event
        (tenant_id,id,created_by,aggregate_type,aggregate_id,aggregate_version,
        event_type,
        schema_version,occurred_at,correlation_id,payload)
        VALUES (:t,:id,:actor,:kind,:target,:version,:action,1,clock_timestamp(),
        :request,CAST(:payload AS jsonb))""",
        {
            "t": ctx.tenant_id,
            "id": event_id,
            "actor": ctx.principal_id,
            "kind": kind,
            "target": target,
            "version": version,
            "action": action,
            "request": ctx.request_id,
            "payload": json.dumps(payload),
        },
    )
    repo.execute(
        """INSERT INTO med.outbox_delivery
        (tenant_id,id,created_by,updated_by,event_id,consumer,state,next_attempt_at)
        VALUES (:t,:id,:actor,:actor,:event,:consumer,'READY',now())""",
        {
            "t": ctx.tenant_id,
            "id": uuid4(),
            "actor": ctx.principal_id,
            "event": event_id,
            "consumer": "settlement_projection"
            if action.startswith("finance.")
            else "inventory_projection",
        },
    )
