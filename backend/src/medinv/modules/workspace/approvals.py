"""Sequential purchase approval with published snapshots, original-order locking and audit."""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import StrictBool

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.workspace.management import ApprovalDesign, Version, require_entity
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class PolicyPublish(DTO):
    entity_id: UUID
    expected_version: Version
    design_id: UUID
    design_version: Version
    enabled: StrictBool = True
    reason: Text


def policy(repo: SqlSession, entity: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    rows = repo.rows(
        "SELECT * FROM med.approval_policy WHERE entity_id=:e" + (" FOR SHARE" if lock else ""),
        {"e": entity},
    )
    return rows[0] if rows else None


def binding(
    repo: SqlSession, order: dict[str, Any], *, lock: bool = False
) -> dict[str, Any] | None:
    rows = repo.rows(
        "SELECT * FROM med.purchase_approval_instance "
        "WHERE order_id=:o AND revision_no=:r" + (" FOR UPDATE" if lock else ""),
        {"o": order["id"], "r": order["revision_no"]},
    )
    return rows[0] if rows else None


def bind(repo: SqlSession, ctx: TenantContext, order: UUID, entity: UUID, revision: int) -> None:
    # Creation/amendment already holds the order row. Copy the published policy.
    repo.execute(
        """UPDATE med.purchase_approval_instance SET state='SUPERSEDED',
        updated_by=:a,updated_at=now() WHERE order_id=:o AND state IN ('PENDING','RETURNED')""",
        {"a": ctx.principal_id, "o": order},
    )
    current = policy(repo, entity, lock=True)
    if not current or not current["enabled"]:
        return
    definition = {
        **current["definition"],
        "design_id": str(current["design_id"]),
        "design_version": str(current["design_version"]),
    }
    target = uuid4()
    repo.execute(
        """INSERT INTO med.purchase_approval_instance
        (tenant_id,id,entity_id,order_id,revision_no,policy_version,definition,state,created_by,updated_by)
        VALUES(:t,:id,:e,:o,:r,:v,CAST(:d AS jsonb),'PENDING',:a,:a)""",
        {
            "t": ctx.tenant_id,
            "id": target,
            "e": entity,
            "o": order,
            "r": revision,
            "v": current["version"],
            "d": json.dumps(definition),
            "a": ctx.principal_id,
        },
    )
    audit(
        repo,
        ctx,
        "purchase_approval_instance",
        target,
        "approval.bound",
        {
            "order_id": str(order),
            "revision_no": revision,
            "policy_version": str(current["version"]),
            "definition": definition,
        },
    )


def eligible(ctx: TenantContext, order: dict[str, Any], instance: dict[str, Any]) -> bool:
    steps = instance["definition"]["steps"]
    return bool(
        instance["state"] == "PENDING"
        and instance["step_index"] < len(steps)
        and steps[instance["step_index"]]["role"] in ctx.role_codes
        and "purchase.approve" in ctx.permissions
        and ctx.principal_id != order["created_by"]
        and ctx.principal_id not in order["revision_editors"]
    )


def display(ctx: TenantContext, order: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return dict(
        jsonable_encoder(
            {
                "id": row["id"],
                "state": row["state"],
                "round": row["round"],
                "revision_no": row["revision_no"],
                "policy_version": str(row["policy_version"]),
                "definition": row["definition"],
                "step_index": row["step_index"],
                "can_approve": eligible(ctx, order, row),
                "can_resubmit": row["state"] == "RETURNED" and "purchase.write" in ctx.permissions,
                "decisions": row["decisions"],
            }
        )
    )


def details(repo: SqlSession, ctx: TenantContext, order: dict[str, Any]) -> dict[str, Any] | None:
    current = binding(repo, order)
    if not current:
        return None
    history = repo.rows(
        """SELECT * FROM med.purchase_approval_instance WHERE order_id=:o
        AND revision_no<>:r ORDER BY revision_no DESC LIMIT 20""",
        {"o": order["id"], "r": order["revision_no"]},
    )
    return {
        **display(ctx, order, current),
        "previous": [display(ctx, order, row) for row in history],
    }


def record_decision(
    repo: SqlSession,
    ctx: TenantContext,
    row: dict[str, Any],
    action: str,
    reason: str,
    state: str,
    step: int,
    round_number: int,
) -> None:
    timestamp = repo.rows("SELECT clock_timestamp() AS value", {})[0]["value"]
    decisions = [
        *row["decisions"],
        jsonable_encoder(
            {
                "action": action,
                "reason": reason,
                "actor": ctx.principal_id,
                "at": timestamp,
                "step_index": row["step_index"],
                "round": row["round"],
                "role": row["definition"]["steps"][row["step_index"]]["role"]
                if action in {"APPROVE", "REJECT"}
                and row["step_index"] < len(row["definition"]["steps"])
                else None,
            }
        ),
    ]
    repo.execute(
        """UPDATE med.purchase_approval_instance SET state=:s,step_index=:step,
        round=:round,decisions=CAST(:d AS jsonb),updated_by=:a,updated_at=now() WHERE id=:id""",
        {
            "id": row["id"],
            "s": state,
            "step": step,
            "round": round_number,
            "d": json.dumps(decisions),
            "a": ctx.principal_id,
        },
    )
    audit(
        repo,
        ctx,
        "purchase_approval_instance",
        row["id"],
        "approval." + action.lower(),
        {
            "order_id": str(row["order_id"]),
            "revision_no": row["revision_no"],
            "policy_version": str(row["policy_version"]),
            "decision": decisions[-1],
            "state": state,
        },
    )


def vote(repo: SqlSession, ctx: TenantContext, order: dict[str, Any], reason: str) -> bool:
    row = binding(repo, order, lock=True)
    if not row:
        return True
    if row["state"] != "PENDING":
        raise BusinessError("APPROVAL_NOT_PENDING", "流程已退回或结束，请核对当前审批状态", 409)
    if not eligible(ctx, order, row):
        raise BusinessError("APPROVAL_ROLE_REQUIRED", "请由当前节点指定角色的独立成员审批", 403)
    next_step = row["step_index"] + 1
    complete = next_step == len(row["definition"]["steps"])
    record_decision(
        repo,
        ctx,
        row,
        "APPROVE",
        reason,
        "APPROVED" if complete else "PENDING",
        next_step,
        row["round"],
    )
    return bool(complete)


def cancel(repo: SqlSession, ctx: TenantContext, order: dict[str, Any], reason: str) -> None:
    row = binding(repo, order, lock=True)
    if row and row["state"] in {"PENDING", "RETURNED"}:
        record_decision(
            repo, ctx, row, "CANCEL", reason, "CANCELLED", row["step_index"], row["round"]
        )


class ApprovalWorkflows:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, entity: UUID) -> dict[str, Any]:
        ctx.require("catalog.configure", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            current = policy(repo, entity)
            if not current:
                return {"enabled": False, "version": "0", "definition": None, "history": []}
            history = repo.rows(
                """SELECT created_at,created_by,reason FROM med.audit_event
                WHERE resource_type='approval_policy' AND resource_id=:id
                ORDER BY created_at DESC,id DESC LIMIT 30""",
                {"id": current["id"]},
            )
            return dict(
                jsonable_encoder(
                    {
                        **current,
                        "version": str(current["version"]),
                        "design_version": str(current["design_version"]),
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in history],
                    }
                )
            )

    def publish(self, ctx: TenantContext, body: PolicyPublish, key: str) -> dict[str, Any]:
        ctx.require("catalog.configure", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            require_entity(repo, body.entity_id)
            repo.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
                {"key": f"approval-policy:{ctx.tenant_id}:{body.entity_id}"},
            )
            rows = repo.rows(
                """SELECT * FROM med.management_record
                WHERE id=:id AND entity_id=:e AND kind='approval_design' FOR SHARE""",
                {"id": body.design_id, "e": body.entity_id},
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "审批设计不可见或不属于本主体", 404)
            design = rows[0]
            before = policy(repo, body.entity_id)
            if body.expected_version != str(
                before["version"] if before else 0
            ) or body.design_version != str(design["version"]):
                raise BusinessError(
                    "VERSION_CONFLICT", "设计或已发布流程已变化，请重新载入核对", 409
                )
            definition = ApprovalDesign.model_validate(design["payload"]).model_dump(mode="json")
            if body.enabled and definition["business"] != "PURCHASE":
                raise BusinessError(
                    "APPROVAL_BUSINESS_UNSUPPORTED",
                    "本期支持采购审批发布，其他业务可保存设计稿",
                    422,
                )
            if body.enabled and any(s["mode"] != "ANY" for s in definition["steps"]):
                raise BusinessError(
                    "APPROVAL_MODE_UNSUPPORTED",
                    "本期采购发布支持顺序或签，会签设计可先保存为设计稿",
                    422,
                )
            if not body.enabled and (not before or before["design_id"] != body.design_id):
                raise BusinessError(
                    "APPROVAL_NOT_ACTIVE", "请选择当前已发布的采购流程进行停用", 409
                )
            # Disabling preserves the publication even when the working design has changed.
            saved_definition, saved_design_version = definition, design["version"]
            if not body.enabled:
                assert before is not None
                saved_definition, saved_design_version = (
                    before["definition"],
                    before["design_version"],
                )
            target = before["id"] if before else uuid4()
            result = repo.rows(
                """INSERT INTO med.approval_policy
                (tenant_id,entity_id,id,enabled,design_id,design_version,definition,updated_by)
                VALUES(:t,:e,:id,:enabled,:design,:dv,CAST(:d AS jsonb),:a)
                ON CONFLICT(tenant_id,entity_id) DO UPDATE SET enabled=EXCLUDED.enabled,
                  design_id=EXCLUDED.design_id,design_version=EXCLUDED.design_version,
                  definition=EXCLUDED.definition,version=med.approval_policy.version+1,
                  updated_by=:a,updated_at=now() RETURNING version""",
                {
                    "t": ctx.tenant_id,
                    "e": body.entity_id,
                    "id": target,
                    "enabled": body.enabled,
                    "design": body.design_id,
                    "dv": saved_design_version,
                    "d": json.dumps(saved_definition),
                    "a": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "approval_policy",
                target,
                "approval.published" if body.enabled else "approval.disabled",
                {
                    "before": jsonable_encoder(before),
                    "after": saved_definition,
                    "reason": body.reason,
                    "version": str(result["version"]),
                    "enabled": body.enabled,
                },
            )
            return {"version": str(result["version"]), "enabled": body.enabled}

        return command(
            self.uow,
            ctx,
            "approval.publish",
            str(body.entity_id),
            key,
            body.model_dump(mode="json"),
            run,
        )

    def transition(
        self,
        ctx: TenantContext,
        order_id: UUID,
        body: Transition,
        key: str,
        action: Literal["reject", "resubmit"],
    ) -> dict[str, Any]:
        permission = "purchase.approve" if action == "reject" else "purchase.write"
        ctx.require(permission)
        # Authorization precedes command-cache replay, including after entity access is removed.
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                "SELECT entity_id FROM med.purchase_order WHERE id=:id", {"id": order_id}
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "采购单不可见或不存在", 404)
            ctx.require(permission, rows[0]["entity_id"])

        def run(repo: SqlSession) -> dict[str, Any]:
            rows = repo.rows(
                "SELECT * FROM med.purchase_order WHERE id=:id FOR UPDATE", {"id": order_id}
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "采购单不可见或不存在", 404)
            order = rows[0]
            ctx.require(permission, order["entity_id"])
            require_entity(repo, order["entity_id"])
            if body.expected_version != str(order["version"]):
                raise BusinessError("VERSION_CONFLICT", "采购单已变化，请刷新", 409)
            row = binding(repo, order, lock=True)
            if not row or order["approval_state"] != "DRAFT":
                raise BusinessError("APPROVAL_NOT_PENDING", "当前采购单没有可办理的审批流程", 409)
            if action == "reject":
                if not eligible(ctx, order, row):
                    raise BusinessError(
                        "APPROVAL_ROLE_REQUIRED", "请由当前节点指定角色的独立成员退回", 403
                    )
                state, step, round_number = "RETURNED", row["step_index"], row["round"]
            else:
                if row["state"] != "RETURNED":
                    raise BusinessError("APPROVAL_NOT_RETURNED", "仅已退回的流程可重新提交", 409)
                state, step, round_number = "PENDING", 0, row["round"] + 1
            record_decision(repo, ctx, row, action.upper(), body.reason, state, step, round_number)
            repo.execute(
                """UPDATE med.purchase_order SET version=version+1,
                revision_editors=CASE WHEN :resubmit THEN
                  ARRAY(SELECT DISTINCT unnest(revision_editors||ARRAY[CAST(:a AS uuid)]))
                  ELSE revision_editors END WHERE id=:id""",
                {"id": order_id, "a": ctx.principal_id, "resubmit": action == "resubmit"},
            )
            audit(
                repo,
                ctx,
                "purchase_order",
                order_id,
                "purchase.approval_" + action,
                {
                    "reason": body.reason,
                    "revision_no": order["revision_no"],
                    "round": round_number,
                    "workflow_state": state,
                },
            )
            return {
                "id": str(order_id),
                "version": str(order["version"] + 1),
                "workflow_state": state,
            }

        return command(
            self.uow,
            ctx,
            "purchase.approval_" + action,
            str(order_id),
            key,
            body.model_dump(mode="json"),
            run,
        )
