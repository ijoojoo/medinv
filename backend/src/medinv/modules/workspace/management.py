"""Enterprise master records, approval design drafts and non-stock quality ledgers."""

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    ValidationError,
    model_validator,
)

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.catalog.application.dictionaries import current_dictionary, dictionary_lock
from medinv.modules.workspace.application.queue import WorkKind, WorkQueue
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Kind = Literal["staff", "settings", "approval_design", "maintenance"]
Note = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=500)]
Short = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=100)]
Version = Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]


class Staff(DTO):
    code: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")]
    name: Text
    department_id: UUID | None = None
    job: Short = ""
    phone: Short = ""
    email: Short = ""
    joined_on: date | None = None
    left_on: date | None = None
    status: Literal["ACTIVE", "LEFT"] = "ACTIVE"
    note: Note = ""

    @model_validator(mode="after")
    def dates(self) -> Staff:
        if self.joined_on and self.left_on and self.left_on < self.joined_on:
            raise ValueError("离职日期不得早于入职日期")
        if self.status == "ACTIVE" and self.left_on:
            raise ValueError("在职状态不能填写离职日期")
        return self


class EnterpriseSettings(DTO):
    reminders_enabled: StrictBool = True
    work_queue_enabled: StrictBool = True
    certificate_notice_days: Annotated[int, Field(strict=True, ge=0, le=365)] = 30
    maintenance_notice_days: Annotated[int, Field(strict=True, ge=0, le=365)] = 7


class ApprovalStep(DTO):
    id: UUID
    name: Text
    role: Literal["QUALITY", "ADMIN", "OWNER"]
    mode: Literal["ANY", "ALL"] = "ANY"


class ApprovalDesign(DTO):
    name: Text
    business: Literal["PURCHASE", "SALES", "PRODUCT", "PARTY", "QUALITY"]
    steps: list[ApprovalStep] = Field(min_length=1, max_length=10)
    note: Note = ""

    @model_validator(mode="after")
    def unique_steps(self) -> ApprovalDesign:
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("审批节点身份不可重复")
        return self


class Maintenance(DTO):
    product_id: UUID
    lot_no: Short = ""
    checked_on: date
    next_due: date | None = None
    type_id: UUID | None = None
    operator_name: Text
    checks: Text
    result: Literal["NORMAL", "ABNORMAL"]
    actions: Note = ""
    state: Literal["DRAFT", "COMPLETED", "VOID"] = "DRAFT"

    @model_validator(mode="after")
    def valid_record(self) -> Maintenance:
        if self.next_due and self.next_due < self.checked_on:
            raise ValueError("下次养护日期不得早于本次日期")
        if self.result == "ABNORMAL" and not self.actions:
            raise ValueError("异常养护必须填写处理说明")
        return self


class ManagementSave(DTO):
    entity_id: UUID
    expected_version: Version
    payload: dict[str, Any]
    reason: Text


class MessageRead(DTO):
    entity_id: UUID
    source: Literal["work", "certificate", "maintenance"]
    message_id: Annotated[StrictStr, Field(min_length=1, max_length=110)]


MODELS: dict[str, type[BaseModel]] = {
    "staff": Staff,
    "settings": EnterpriseSettings,
    "approval_design": ApprovalDesign,
    "maintenance": Maintenance,
}
PERMISSIONS = {
    "staff": ("members.read", "members.manage"),
    "settings": ("catalog.read", "catalog.configure"),
    "approval_design": ("catalog.configure", "catalog.configure"),
    "maintenance": ("quality.read", "quality.write"),
}


def require_entity(repo: SqlSession, entity: UUID) -> None:
    if not repo.rows(
        "SELECT id FROM med.legal_entity WHERE id=:e AND status='ACTIVE'", {"e": entity}
    ):
        raise BusinessError("NOT_FOUND", "经营主体不存在或已停用", 404)


class Management:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(
        self, ctx: TenantContext, kind: Kind, entity: UUID, search: str = "", offset: int = 0
    ) -> dict[str, Any]:
        ctx.require(PERMISSIONS[kind][0], entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            rows = repo.rows(
                """SELECT id,payload,version,updated_at,updated_by FROM med.management_record
                WHERE entity_id=:e AND kind=:k AND (:q='' OR
                strpos(lower(payload::text),lower(:q))>0)
                ORDER BY updated_at DESC,id LIMIT 51 OFFSET :o""",
                {"e": entity, "k": kind, "q": search, "o": offset},
            )
            return dict(
                jsonable_encoder(
                    {
                        "items": [{**r, "version": str(r["version"])} for r in rows[:50]],
                        "has_more": len(rows) > 50,
                    }
                )
            )

    def detail(self, ctx: TenantContext, kind: Kind, entity: UUID, target: UUID) -> dict[str, Any]:
        ctx.require(PERMISSIONS[kind][0], entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rows = repo.rows(
                "SELECT * FROM med.management_record WHERE id=:id AND kind=:k AND entity_id=:e",
                {"id": target, "k": kind, "e": entity},
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "记录不可见或不存在", 404)
            history = repo.rows(
                """SELECT created_at,created_by,reason FROM med.audit_event
                WHERE resource_type='management_record' AND resource_id=:id ORDER BY created_at
                DESC,id LIMIT 30""",
                {"id": target},
            )
            return dict(
                jsonable_encoder(
                    {
                        **rows[0],
                        "version": str(rows[0]["version"]),
                        "history": [{**h, "detail": json.loads(h["reason"])} for h in history],
                    }
                )
            )

    def save(
        self, ctx: TenantContext, kind: Kind, target: UUID, body: ManagementSave, key: str
    ) -> dict[str, Any]:
        ctx.require(PERMISSIONS[kind][1], body.entity_id)
        try:
            payload = MODELS[kind].model_validate(body.payload).model_dump(mode="json")
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            first = errors[0]
            raise BusinessError(
                "INVALID_MANAGEMENT_RECORD",
                f"{'.'.join(map(str, first['loc']))}: {first['msg']}",
                422,
            ) from exc

        def run(repo: SqlSession) -> dict[str, Any]:
            require_entity(repo, body.entity_id)
            repo.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:s,0))",
                {"s": f"management:{ctx.tenant_id}:{body.entity_id}:{kind}"},
            )
            rows = repo.rows(
                "SELECT * FROM med.management_record WHERE id=:id AND kind=:k AND "
                "entity_id=:e FOR UPDATE",
                {"id": target, "k": kind, "e": body.entity_id},
            )
            before = rows[0] if rows else None
            if str(before["version"] if before else 0) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "记录已变化，请重新加载后修改", 409)
            record_key = payload.get("code", str(target)) if kind != "settings" else "enterprise"
            duplicates = repo.rows(
                "SELECT id FROM med.management_record WHERE entity_id=:e AND kind=:k AND "
                "record_key=:r AND id<>:id",
                {"e": body.entity_id, "k": kind, "r": record_key, "id": target},
            )
            if duplicates:
                raise BusinessError(
                    "DUPLICATE_RECORD", "职员编码或企业设置已存在，请打开原记录维护", 409
                )
            if kind == "staff" and before and before["payload"]["code"] != payload["code"]:
                raise BusinessError("IMMUTABLE_CODE", "职员编码保存后不可修改", 422)
            if kind in {"staff", "maintenance"}:
                field, dictionary = (
                    ("department_id", "department")
                    if kind == "staff"
                    else ("type_id", "maintenance_type")
                )
                if payload.get(field) and (
                    not before or before["payload"].get(field) != payload[field]
                ):
                    dictionary_lock(repo, ctx)
                    if not any(
                        i["id"] == payload[field] and i["active"]
                        for i in current_dictionary(repo, dictionary)["items"]
                    ):
                        raise BusinessError(
                            "DICTIONARY_VALUE_INVALID", "请选择当前启用的部门或养护类型", 422
                        )
            if kind == "maintenance":
                if not repo.rows(
                    "SELECT id FROM med.product WHERE id=:id", {"id": payload["product_id"]}
                ):
                    raise BusinessError("NOT_FOUND", "养护商品不可见或不存在", 404)
                if payload["state"] == "VOID" and not before:
                    raise BusinessError("INVALID_STATE", "未保存记录不能作废", 422)
                if before and before["payload"]["state"] in {"COMPLETED", "VOID"}:
                    if before["payload"]["state"] == "VOID" or payload != {
                        **before["payload"],
                        "state": "VOID",
                    }:
                        raise BusinessError(
                            "RECORD_IMMUTABLE",
                            "已完成养护记录只能注明原因作废，不能覆写原记录",
                            422,
                        )
            row = repo.rows(
                """INSERT INTO
med.management_record(tenant_id,id,entity_id,kind,record_key,payload,created_by,updated_by)
                VALUES(:t,:id,:e,:k,:r,CAST(:p AS jsonb),:a,:a)
                ON CONFLICT(tenant_id,id) DO UPDATE SET
                payload=EXCLUDED.payload,version=management_record.version+1,
                updated_by=:a,updated_at=clock_timestamp() WHERE management_record.kind=:k AND
                management_record.entity_id=:e
                RETURNING id,version""",
                {
                    "t": ctx.tenant_id,
                    "id": target,
                    "e": body.entity_id,
                    "k": kind,
                    "r": record_key,
                    "p": json.dumps(payload),
                    "a": ctx.principal_id,
                },
            )
            if not row:
                raise BusinessError("NOT_FOUND", "记录不可见或不存在", 404)
            audit(
                repo,
                ctx,
                "management_record",
                target,
                f"management.{kind}.saved",
                {
                    "before": before["payload"] if before else None,
                    "after": payload,
                    "reason": body.reason,
                    "version": str(row[0]["version"]),
                },
            )
            return {"id": str(target), "version": str(row[0]["version"])}

        return command(
            self.uow,
            ctx,
            f"management.{kind}.save",
            str(target),
            key,
            body.model_dump(mode="json"),
            run,
        )

    def quality_records(
        self, ctx: TenantContext, entity: UUID, search: str, offset: int
    ) -> dict[str, Any]:
        ctx.require("quality.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            rows = repo.rows(
                """SELECT l.id,i.id AS inspection_id,i.inspected_at,i.created_by,i.note,
                r.number AS receipt_number,o.number AS order_number,p.name AS party_name,
                l.qualified_qty,l.rejected_qty,pr.name AS product_name,s.base_unit,lot.lot_no
                FROM med.inspection i JOIN med.receipt r ON
                (r.tenant_id,r.id)=(i.tenant_id,i.receipt_id)
                JOIN med.purchase_order o ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
                JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
                JOIN med.inspection_line l ON (l.tenant_id,l.inspection_id)=(i.tenant_id,i.id)
                JOIN med.receipt_line rl ON (rl.tenant_id,rl.id)=(l.tenant_id,l.receipt_line_id)
                JOIN med.sku s ON (s.tenant_id,s.id)=(rl.tenant_id,rl.sku_id)
                JOIN med.product pr ON (pr.tenant_id,pr.id)=(s.tenant_id,s.product_id)
                JOIN med.lot lot ON (lot.tenant_id,lot.id)=(rl.tenant_id,rl.lot_id)
                WHERE o.entity_id=:e AND (:q='' OR
                strpos(lower(p.name||' '||o.number||' '||r.number),lower(:q))>0)
                ORDER BY i.inspected_at DESC,l.id LIMIT 51 OFFSET :o""",
                {"e": entity, "q": search, "o": offset},
            )
            return dict(jsonable_encoder({"items": rows[:50], "has_more": len(rows) > 50}))

    def messages(
        self, ctx: TenantContext, entity: UUID, source: str, offset: int, target: str = ""
    ) -> dict[str, Any]:
        ctx.require("tenant.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            settings = repo.rows(
                "SELECT payload FROM med.management_record WHERE entity_id=:e AND kind='settings'",
                {"e": entity},
            )
            config = EnterpriseSettings.model_validate(
                settings[0]["payload"] if settings else {}
            ).model_dump()
            if not config["reminders_enabled"]:
                return {"items": [], "has_more": False, "disabled": True}
            if source == "work":
                rows = []
            elif source == "certificate":
                ctx.require("catalog.read", entity)
                rows = repo.rows(
                    """SELECT 'certificate:'||c.id||':'||c.version AS message_id,
                  p.name||' · '||c.number AS title,c.valid_until AS due,'parties' AS
                  page,c.party_id AS target_id
                  FROM med.party_certificate c JOIN med.party p ON
                  (p.tenant_id,p.id)=(c.tenant_id,c.party_id)
                  WHERE p.entity_id=:e AND c.state<>'INACTIVE' AND c.validity_mode='FIXED'
                  AND c.valid_until <= (now() AT TIME ZONE 'Asia/Shanghai')::date + :days
                  AND (:target='' OR 'certificate:'||c.id||':'||c.version=:target)
                  ORDER BY c.valid_until,c.id LIMIT 51 OFFSET :o""",
                    {
                        "e": entity,
                        "days": config["certificate_notice_days"],
                        "o": offset,
                        "target": target,
                    },
                )
            else:
                ctx.require("quality.read", entity)
                rows = repo.rows(
                    """WITH latest AS (
                  SELECT DISTINCT ON (payload->>'product_id',payload->>'lot_no') * FROM
                  med.management_record
                  WHERE entity_id=:e AND kind='maintenance' AND payload->>'state'='COMPLETED'
                  ORDER BY payload->>'product_id',payload->>'lot_no',payload->>'checked_on'
                  DESC,updated_at DESC,id)
                  SELECT 'maintenance:'||r.id||':'||r.version AS
                  message_id,p.name||' · '||(r.payload->>'lot_no') AS title,
                  r.payload->>'next_due' AS due,'quality-ledgers' AS page,r.id AS target_id
                  FROM latest r JOIN med.product p ON p.id=(r.payload->>'product_id')::uuid AND
                  p.tenant_id=r.tenant_id
                  WHERE (r.payload->>'next_due')::date <= (now() AT TIME ZONE
                  'Asia/Shanghai')::date + :days
                  AND (:target='' OR 'maintenance:'||r.id||':'||r.version=:target)
                  ORDER BY r.payload->>'next_due',r.id LIMIT 51 OFFSET :o""",
                    {
                        "e": entity,
                        "days": config["maintenance_notice_days"],
                        "o": offset,
                        "target": target,
                    },
                )
        more = len(rows) > 50
        if source == "work" and config["work_queue_enabled"]:
            queue = WorkQueue(self.uow).listing(ctx, entity, WorkKind.ALL, "", offset)
            names = {k["code"]: k["name"] for k in queue["kinds"]}
            rows = [
                {
                    "message_id": "work:" + r["kind"] + ":" + r["item_id"],
                    "title": names[r["kind"]] + " · " + r["number"],
                    "due": r["document_time"],
                    "page": r["page"],
                    "target_id": r["target_id"],
                    "work_item": r,
                }
                for r in queue["items"]
            ]
            if target:
                rows = [r for r in rows if r["message_id"] == target]
            more = queue["has_more"]
        with self.uow.open(ctx, readonly=True) as session:
            read_rows = SqlSession(session).rows(
                "SELECT payload->>'message_id' AS id FROM med.management_record WHERE "
                "entity_id=:e AND kind='notification_read' AND created_by=:a "
                "AND record_key=ANY(:keys)",
                {
                    "e": entity,
                    "a": ctx.principal_id,
                    "keys": [str(ctx.principal_id) + ":" + r["message_id"] for r in rows[:50]],
                },
            )
            read = {r["id"] for r in read_rows}
        return dict(
            jsonable_encoder(
                {
                    "items": [{**r, "read": r["message_id"] in read} for r in rows[:50]],
                    "has_more": more,
                    "disabled": False,
                }
            )
        )

    def mark_read(self, ctx: TenantContext, body: MessageRead, key: str) -> dict[str, Any]:
        ctx.require("tenant.read", body.entity_id)
        # A read marker only affects the actor's inbox; it never acknowledges a business task.
        if not body.message_id.startswith(body.source + ":"):
            raise BusinessError("MESSAGE_INVALID", "消息来源不匹配", 422)

        def run(repo: SqlSession) -> dict[str, Any]:
            require_entity(repo, body.entity_id)
            repo.execute(
                """INSERT INTO
med.management_record(tenant_id,id,entity_id,kind,record_key,payload,created_by,updated_by)
                VALUES(:t,:id,:e,'notification_read',:r,CAST(:p AS jsonb),:a,:a)
                ON CONFLICT(tenant_id,entity_id,kind,record_key) DO NOTHING""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "e": body.entity_id,
                    "r": str(ctx.principal_id) + ":" + body.message_id,
                    "p": json.dumps({"message_id": body.message_id}),
                    "a": ctx.principal_id,
                },
            )
            return {"read": True}

        return command(
            self.uow, ctx, "message.read", body.message_id, key, body.model_dump(mode="json"), run
        )
