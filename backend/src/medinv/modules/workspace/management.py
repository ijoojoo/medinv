"""Enterprise master records, approval design drafts and non-stock quality ledgers."""

import json
from datetime import date
from hashlib import sha256
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
    certificate_reminders_enabled: StrictBool = True
    scope_reminders_enabled: StrictBool = True
    catalog_review_reminders_enabled: StrictBool = True
    maintenance_reminders_enabled: StrictBool = True
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
        if self.business in {"PRODUCT", "PARTY"} and len(self.steps) != 1:
            raise ValueError("商品及往来单位资料随主体一次审核，仅保留一个统一审核节点")
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
    source: Literal["work", "certificate", "scope", "catalog_review", "maintenance"]
    message_id: Annotated[StrictStr, Field(min_length=1, max_length=250)]


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
        from medinv.modules.workspace.quality_records import QualityRecordQuery, QualityRecords

        return QualityRecords(self.uow).listing(
            ctx, QualityRecordQuery(entity_id=entity, search=search, offset=offset)
        )

    def messages(
        self, ctx: TenantContext, entity: UUID, source: str, offset: int, target: str = ""
    ) -> dict[str, Any]:
        from medinv.modules.workspace.reminders import Reminders

        return Reminders(self.uow).listing(ctx, entity, source, offset, target)

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
            self.uow,
            ctx,
            "message.read",
            body.message_id
            if len(body.message_id) <= 100
            else sha256(body.message_id.encode()).hexdigest(),
            key,
            body.model_dump(mode="json"),
            run,
        )
