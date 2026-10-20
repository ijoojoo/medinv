"""Versioned, private document-entry preferences; never alter business data."""

from typing import Annotated, Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from medinv.modules.catalog.api.schemas import DTO
from medinv.modules.workspace.drafts import Channel, permission
from medinv.platform.database.commands import command
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

KEYS = {"product", "specification", "manufacturer_name", "pack", "qty", "price", "tax", "amount"}


class EntryColumn(DTO):
    key: Literal[
        "product", "specification", "manufacturer_name", "pack", "qty", "price", "tax", "amount"
    ]
    label: Annotated[StrictStr, Field(min_length=1, max_length=30)]
    visible: StrictBool
    tab_stop: StrictBool
    width_mode: Literal["px", "weight"]
    width: Annotated[StrictInt, Field(ge=1, le=600)]
    decimals: Annotated[StrictInt, Field(ge=-1, le=6)] = -1
    grouping: StrictBool = False
    wrap: StrictBool = False
    tax_display: Literal["decimal", "percent"] = "decimal"

    @model_validator(mode="after")
    def valid_field(self) -> EntryColumn:
        if not self.label.strip():
            raise ValueError("字段名称不能为空")
        if self.width_mode == "px" and self.width < (220 if self.key == "product" else 90):
            raise ValueError("列宽不足")
        if self.width_mode == "weight" and self.width > 20:
            raise ValueError("宽度比例须在1到20之间")
        if self.key in {"product", "qty", "price"} and not self.visible:
            raise ValueError("商品、数量、含税单价必须显示")
        if self.key in {"product", "qty"} and not self.tab_stop:
            raise ValueError("商品和数量必须停留")
        if self.key not in {"product", "qty", "price", "pack", "tax"} and self.tab_stop:
            raise ValueError("只读字段不能停留")
        if self.key not in {"qty", "price", "amount"} and (self.decimals != -1 or self.grouping):
            raise ValueError("该字段不支持数字格式")
        if self.key != "tax" and self.tax_display != "decimal":
            raise ValueError("仅税率支持百分比显示")
        if self.key not in {"product", "specification", "manufacturer_name"} and self.wrap:
            raise ValueError("仅文本字段支持换行")
        return self


class EntryTemplate(DTO):
    schema_version: Literal[2] = 2
    columns: list[EntryColumn] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def complete_columns(self) -> EntryTemplate:
        if {c.key for c in self.columns} != KEYS:
            raise ValueError("字段必须完整且不能重复")
        return self


class TemplateSave(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    template: EntryTemplate


class EntryTemplates:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def detail(self, ctx: TenantContext, channel: Channel) -> dict[str, Any]:
        ctx.require(permission(channel))
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                "SELECT version,template FROM med.entry_template "
                "WHERE owner_id=:owner AND channel=:channel",
                {"owner": ctx.principal_id, "channel": channel},
            )
            return (
                {"version": str(rows[0]["version"]), "template": rows[0]["template"]}
                if rows
                else {"version": "0", "template": None}
            )

    def save(
        self, ctx: TenantContext, channel: Channel, body: TemplateSave, key: str
    ) -> dict[str, Any]:
        ctx.require(permission(channel))

        def execute(repo: SqlSession) -> dict[str, Any]:
            rows = (
                repo.rows(
                    """INSERT INTO med.entry_template(tenant_id,owner_id,channel,template)
                SELECT :tenant,:owner,:channel,CAST(:template AS jsonb) WHERE :version='0'
                ON CONFLICT(tenant_id,owner_id,channel) DO NOTHING RETURNING version""",
                    {
                        "tenant": ctx.tenant_id,
                        "owner": ctx.principal_id,
                        "channel": channel,
                        "template": body.template.model_dump_json(),
                        "version": body.expected_version,
                    },
                )
                if body.expected_version == "0"
                else repo.rows(
                    """UPDATE med.entry_template SET template=CAST(:template AS jsonb),
                version=version+1,updated_at=clock_timestamp()
                WHERE owner_id=:owner AND channel=:channel AND version=CAST(:version AS bigint)
                RETURNING version""",
                    {
                        "owner": ctx.principal_id,
                        "channel": channel,
                        "version": body.expected_version,
                        "template": body.template.model_dump_json(),
                    },
                )
            )
            if not rows:
                raise BusinessError(
                    "TEMPLATE_VERSION_CONFLICT", "字段设置已在其他窗口更新，请重新读取后调整", 409
                )
            return {
                "version": str(rows[0]["version"]),
                "template": body.template.model_dump(mode="json"),
            }

        return command(
            self.uow,
            ctx,
            "entry_template.save",
            f"{ctx.principal_id}:{channel}",
            key,
            body.model_dump(mode="json"),
            execute,
        )
