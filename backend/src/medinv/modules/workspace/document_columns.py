"""Private, entity-scoped display preferences with optimistic locking and exact retry."""

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr

from medinv.modules.catalog.api.schemas import DTO
from medinv.modules.catalog.application.master_config import current_schema
from medinv.modules.workspace.document_fields import Mode, Stage, registry
from medinv.platform.database.commands import command
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class Column(DTO):
    key: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    label: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    visible: StrictBool
    tab_stop: StrictBool = False
    width_mode: Literal["px", "weight"] = "px"
    width: Annotated[StrictInt, Field(ge=1, le=600)]
    decimals: Annotated[StrictInt, Field(ge=-1, le=6)] = -1
    grouping: StrictBool = False
    wrap: StrictBool = False
    tax_display: Literal["decimal", "percent"] = "decimal"


class ColumnSave(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    schema_version: Literal[1] = 1
    columns: list[Column] = Field(min_length=1, max_length=220)


SUBJECT_ALIASES = {"authorization_holder", "registrant_name", "filing_holder"}
SUBJECT_LABELS = {"上市许可持有人", "注册人", "备案人"}


def merge_subject_columns(
    columns: list[dict[str, Any]], fields: list[dict[str, Any]], *, strict: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read-through compatibility, retaining the first visible legacy column's position/style."""
    if strict:
        keys = [c.get("key") for c in columns]
        if len(keys) != len(set(keys)):
            raise BusinessError("COLUMN_INVALID", "字段不适用于当前单据或存在重复列", 422)
    aliases = [c for c in columns if c.get("key") in SUBJECT_ALIASES]
    if not aliases:
        return columns, []
    canonical = next((c for c in columns if c.get("key") == "regulatory_subject"), None)
    selected = canonical or next((c for c in aliases if c.get("visible")), aliases[0])
    label = next(f["label"] for f in fields if f["key"] == "regulatory_subject")
    result = []
    for raw in columns:
        if raw is selected:
            value = {**raw, "key": "regulatory_subject"}
            if not canonical and raw.get("label", "").strip() in SUBJECT_LABELS:
                value["label"] = label
            result.append(value)
        elif raw.get("key") not in SUBJECT_ALIASES | {"regulatory_subject"}:
            result.append(raw)
    return result, ["原持有人、注册人、备案人列已合并；保留原显示位置、宽度及自定义名称。"]


def normalize(
    columns: list[dict[str, Any]], fields: list[dict[str, Any]], *, strict: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    known = {f["key"]: f for f in fields}
    columns, notices = merge_subject_columns(columns, fields, strict=strict)
    result, used = [], set()
    for raw in columns:
        key = raw.get("key")
        if key not in known or key in used:
            if strict:
                raise BusinessError("COLUMN_INVALID", "字段不适用于当前单据或存在重复列", 422)
            notices.append(f"已忽略旧配置字段：{key}")
            continue
        f = known[key]
        value = {**f["default"], **raw}
        value["label"] = value["label"].strip()
        invalid = (
            not value["label"]
            or (value["width_mode"] == "px" and value["width"] < (220 if key == "product" else 90))
            or (value["width_mode"] == "weight" and value["width"] > 20)
            or (f["required"] and not value["visible"])
            or (value["tab_stop"] and not f["editable"])
            or (f["stop_required"] and not value["tab_stop"])
            or (f["type"] != "number" and (value["decimals"] != -1 or value["grouping"]))
            or (f["type"] != "text" and value["wrap"])
            or (key != "tax" and value["tax_display"] != "decimal")
        )
        if invalid:
            if strict:
                raise BusinessError("COLUMN_INVALID", "列宽、格式、停留或必填字段设置无效", 422)
            notices.append(f"已修复旧配置字段：{f['label']}")
            value = f["default"].copy()
        value["required"], value["editable"] = f["required"], f["editable"]
        result.append(value)
        used.add(key)
    for key, f in known.items():
        if key not in used:
            if strict and f["required"]:
                raise BusinessError("COLUMN_REQUIRED", f"必须保留 {f['label']} 列", 422)
            result.append({**f["default"], "required": f["required"], "editable": f["editable"]})
    return result, notices


class DocumentColumns:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def detail(self, ctx: TenantContext, entity: UUID, stage: Stage, mode: Mode) -> dict[str, Any]:
        ctx.require("sales.read" if stage == "sales_orders" else "purchase.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            fields = registry(stage, mode, current_schema(repo)["payload"], ctx.permissions)
            rows = repo.rows(
                "SELECT template,version FROM med.document_column_profile "
                "WHERE entity_id=:e AND owner_id=:o AND document_type=:s AND mode=:m",
                {"e": entity, "o": ctx.principal_id, "s": stage, "m": mode},
            )
            source = "personal" if rows else "system"
            columns = rows[0]["template"]["columns"] if rows else []
            # Read-through migration: do not overwrite the old purchase/sales preference.
            if not rows and stage in {"orders", "sales_orders"} and mode == "editor":
                old = repo.rows(
                    "SELECT template FROM med.entry_template "
                    "WHERE owner_id=:o AND channel=:channel",
                    {
                        "o": ctx.principal_id,
                        "channel": "sale" if stage == "sales_orders" else "purchase",
                    },
                )
                if old:
                    columns = old[0]["template"]["columns"]
                    source = "legacy"
            normalized, notices = normalize(columns, fields)
            if source == "legacy":
                notices.append("已继承原单据录入列设置；保存后按当前主体独立保存。")
            return dict(
                version=str(rows[0]["version"]) if rows else "0",
                schema_version=1,
                columns=normalized,
                fields=fields,
                notices=notices,
                source=source,
            )

    def save(
        self, ctx: TenantContext, entity: UUID, stage: Stage, mode: Mode, body: ColumnSave, key: str
    ) -> dict[str, Any]:
        ctx.require("sales.read" if stage == "sales_orders" else "purchase.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            fields = registry(
                stage, mode, current_schema(SqlSession(session))["payload"], ctx.permissions
            )
        normalized, _ = normalize([c.model_dump() for c in body.columns], fields, strict=True)
        template = json.dumps(
            {
                "schema_version": 1,
                "columns": [
                    {k: v for k, v in c.items() if k not in {"required", "editable"}}
                    for c in normalized
                ],
            }
        )

        def execute(repo: SqlSession) -> dict[str, Any]:
            params = dict(
                t=ctx.tenant_id,
                e=entity,
                o=ctx.principal_id,
                s=stage,
                m=mode,
                v=body.expected_version,
                template=template,
            )
            if body.expected_version == "0":
                rows = repo.rows(
                    """INSERT INTO med.document_column_profile
                    (tenant_id,entity_id,owner_id,document_type,mode,template)
                    VALUES(:t,:e,:o,:s,:m,CAST(:template AS jsonb))
                    ON CONFLICT DO NOTHING RETURNING version""",
                    params,
                )
            else:
                rows = repo.rows(
                    """UPDATE med.document_column_profile SET template=CAST(:template AS jsonb),
                    version=version+1,updated_at=clock_timestamp()
                    WHERE entity_id=:e AND owner_id=:o AND document_type=:s AND mode=:m
                    AND version=CAST(:v AS bigint) RETURNING version""",
                    params,
                )
            if not rows:
                raise BusinessError(
                    "COLUMN_VERSION_CONFLICT", "列配置已在其他窗口更新，请重新读取后调整", 409
                )
            return dict(version=str(rows[0]["version"]), columns=normalized)

        return command(
            self.uow,
            ctx,
            "document_columns.save",
            f"{ctx.principal_id}:{entity}:{stage}:{mode}",
            key,
            body.model_dump(mode="json"),
            execute,
        )
