"""Private editing drafts; conversion is committed with the business command."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictStr,
    model_serializer,
    model_validator,
)

from medinv.modules.catalog.api.schemas import DTO, Version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Channel = Literal["purchase", "sale"]
DraftChannel = Literal["all", "purchase", "sale"]
Short = Annotated[StrictStr, Field(max_length=80)]
Identifier = Annotated[StrictStr, Field(pattern=r"^([0-9a-fA-F-]{36})?$")]


class DraftReference(DTO):
    id: UUID
    version: Version


class DraftLinked(DTO):
    editor_draft: DraftReference | None = None

    @model_serializer(mode="wrap")
    def without_absent_draft(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = dict(handler(self))
        if self.editor_draft is None:
            result.pop("editor_draft", None)
        return result


class DraftLine(DTO):
    client_key: UUID
    sku_id: Identifier = ""
    packaging_id: Identifier = ""
    qty: Short = ""
    unit_price: Short = ""
    tax_rate: Short = "0.13"
    search_text: Annotated[StrictStr, Field(max_length=300)] = ""


class DraftContent(DTO):
    supplier_id: Identifier = ""
    customer_id: Identifier = ""
    warehouse_id: Identifier = ""
    delivery_address: Annotated[StrictStr, Field(max_length=500)] = ""
    minimum_shelf_days: Short = "0"
    credit_days: Short = "30"
    reason: Annotated[StrictStr, Field(max_length=500)] = ""
    lines: list[DraftLine] = Field(max_length=50)

    @model_validator(mode="after")
    def distinct_keys(self) -> DraftContent:
        if len({r.client_key for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate editor row key")
        return self


class DraftSave(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    channel: Channel
    entity_id: UUID
    source_order_id: UUID | None = None
    source_version: Version | None = None
    title: Annotated[StrictStr, Field(max_length=120)] = ""
    content: DraftContent

    @model_validator(mode="after")
    def source_pair(self) -> DraftSave:
        if (self.source_order_id is None) != (self.source_version is None):
            raise ValueError("source order and version must be paired")
        return self


class DraftDiscard(DTO):
    expected_version: Version


def permission(channel: str) -> str:
    return "purchase.write" if channel == "purchase" else "sales.write"


def owned(
    repo: SqlSession, ctx: TenantContext, draft_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    rows = repo.rows(
        "SELECT * FROM med.document_draft WHERE id=:id AND created_by=:actor"
        + (" FOR UPDATE" if lock else ""),
        {"id": draft_id, "actor": ctx.principal_id},
    )
    if not rows:
        raise BusinessError("NOT_FOUND", "草稿不存在或无权访问", 404)
    row = rows[0]
    ctx.require(permission(row["channel"]), row["entity_id"])
    return row


def wire(row: dict[str, Any]) -> dict[str, Any]:
    return dict(jsonable_encoder({**row, "version": str(row["version"])}))


def finish_draft(
    repo: SqlSession,
    ctx: TenantContext,
    link: DraftReference | None,
    channel: Channel,
    entity_id: UUID,
    order_id: UUID,
    source_order_id: UUID | None = None,
    source_version: str | None = None,
) -> None:
    if link is None:
        return
    row = owned(repo, ctx, link.id, lock=True)
    if row["state"] != "EDITING":
        raise BusinessError("DRAFT_CLOSED", "该草稿已转单或已放弃，请打开原单核对", 409)
    if str(row["version"]) != link.version:
        raise BusinessError("DRAFT_VERSION_CONFLICT", "草稿已在其他窗口更新，当前内容仍保留", 409)
    if (
        row["channel"] != channel
        or row["entity_id"] != entity_id
        or row["source_order_id"] != source_order_id
        or row["source_version"] != source_version
    ):
        raise BusinessError("DRAFT_SOURCE_MISMATCH", "草稿与当前单据或原版本不匹配", 409)
    repo.execute(
        """UPDATE med.document_draft SET state='CONVERTED',converted_order_id=:order,
        version=version+1,updated_at=clock_timestamp() WHERE id=:id""",
        {"id": link.id, "order": order_id},
    )
    audit(repo, ctx, "document_draft", link.id, "draft.converted", {"order_id": str(order_id)})


class DocumentDrafts:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(
        self,
        ctx: TenantContext,
        channel: DraftChannel,
        entity: UUID,
        offset: int,
        *,
        search: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        channels = [
            c
            for c in ("purchase", "sale")
            if (channel == "all" or channel == c) and permission(c) in ctx.permissions
        ]
        if not channels:
            raise BusinessError("FORBIDDEN", "没有读取编辑草稿的权限", 403)
        ctx.require(permission(channels[0]), entity)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            if not repo.rows("SELECT id FROM med.legal_entity WHERE id=:id", {"id": entity}):
                raise BusinessError("NOT_FOUND", "经营主体不存在", 404)
            # Draft identifiers are unfinished user input. Compare as text instead of
            # casting JSON values to UUID; bad or stale references remain editable.
            source = """SELECT d.id,d.channel,d.entity_id,d.title,d.source_order_id,
              d.source_version,d.version,d.state,d.created_at,d.updated_at,
              coalesce(p.name,'') AS party_name,coalesce(w.name,'') AS warehouse_name,
              coalesce(po.number,so.number,'') AS source_number,
              (SELECT count(*) FROM jsonb_array_elements(d.content->'lines') l
                WHERE coalesce(l->>'sku_id','')<>'') AS line_count
              FROM med.document_draft d
              LEFT JOIN med.party p ON p.tenant_id=d.tenant_id AND p.entity_id=d.entity_id
                AND p.id::text=lower(CASE WHEN d.channel='purchase'
                  THEN d.content->>'supplier_id' ELSE d.content->>'customer_id' END)
              LEFT JOIN med.warehouse w ON w.tenant_id=d.tenant_id AND w.entity_id=d.entity_id
                AND w.id::text=lower(d.content->>'warehouse_id')
              LEFT JOIN med.purchase_order po
                ON (po.tenant_id,po.id)=(d.tenant_id,d.source_order_id)
                AND po.entity_id=d.entity_id AND d.channel='purchase'
              LEFT JOIN med.sales_order so ON (so.tenant_id,so.id)=(d.tenant_id,d.source_order_id)
                AND so.entity_id=d.entity_id AND d.channel='sale'
              WHERE d.created_by=:actor AND d.entity_id=:entity AND d.state='EDITING'
                AND ((:purchase AND d.channel='purchase') OR (:sale AND d.channel='sale'))"""
            filtered = f"""WITH summary AS ({source}) SELECT * FROM summary
              WHERE :q='' OR strpos(lower(title),lower(:q))>0
                OR strpos(lower(party_name),lower(:q))>0
                OR strpos(lower(warehouse_name),lower(:q))>0
                OR strpos(lower(source_number),lower(:q))>0"""
            params = {
                "actor": ctx.principal_id,
                "entity": entity,
                "q": search.strip(),
                "purchase": "purchase" in channels,
                "sale": "sale" in channels,
                "offset": offset,
                "limit": limit,
            }
            total = repo.rows(f"SELECT count(*) AS total FROM ({filtered}) matches", params)[0][
                "total"
            ]
            rows = repo.rows(
                filtered + " ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset",
                params,
            )
            return {
                "items": [wire(r) for r in rows],
                "total": total,
                "has_more": offset + len(rows) < total,
            }

    def detail(self, ctx: TenantContext, draft_id: UUID) -> dict[str, Any]:
        with self.uow.open(ctx, readonly=True) as session:
            return wire(owned(SqlSession(session), ctx, draft_id))

    def save(self, ctx: TenantContext, draft_id: UUID, body: DraftSave, key: str) -> dict[str, Any]:
        ctx.require(permission(body.channel), body.entity_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            if body.expected_version == "0":
                if not repo.rows(
                    "SELECT id FROM med.legal_entity WHERE id=:id", {"id": body.entity_id}
                ):
                    raise BusinessError("NOT_FOUND", "经营主体不存在", 404)
                if body.source_order_id:
                    table = "purchase_order" if body.channel == "purchase" else "sales_order"
                    source = repo.rows(
                        f"SELECT id,version FROM med.{table} WHERE id=:id AND entity_id=:entity",
                        {"id": body.source_order_id, "entity": body.entity_id},
                    )
                    if not source:
                        raise BusinessError("NOT_FOUND", "原单不存在或不属于该主体", 404)
                    if str(source[0]["version"]) != body.source_version:
                        raise BusinessError("VERSION_CONFLICT", "原单已更新，请重新打开修改", 409)
                inserted = repo.rows(
                    """INSERT INTO med.document_draft(tenant_id,id,entity_id,channel,created_by,
                    source_order_id,source_version,title,content)
                    VALUES(:tenant,:id,:entity,:channel,:actor,:source,:source_version,:title,
                    CAST(:content AS jsonb)) ON CONFLICT(tenant_id,id) DO NOTHING RETURNING id""",
                    {
                        "tenant": ctx.tenant_id,
                        "id": draft_id,
                        "entity": body.entity_id,
                        "channel": body.channel,
                        "actor": ctx.principal_id,
                        "source": body.source_order_id,
                        "source_version": body.source_version,
                        "title": body.title,
                        "content": body.content.model_dump_json(),
                    },
                )
                if not inserted:
                    raise BusinessError(
                        "DRAFT_VERSION_CONFLICT", "草稿已存在，请重新读取后继续", 409
                    )
                audit(
                    repo,
                    ctx,
                    "document_draft",
                    draft_id,
                    "draft.created",
                    {"channel": body.channel},
                )
            else:
                row = owned(repo, ctx, draft_id, lock=True)
                if row["state"] != "EDITING":
                    raise BusinessError("DRAFT_CLOSED", "该草稿已转单或已放弃", 409)
                if str(row["version"]) != body.expected_version:
                    raise BusinessError(
                        "DRAFT_VERSION_CONFLICT", "草稿已在其他窗口更新，不能覆盖", 409
                    )
                if any(
                    row[k] != getattr(body, k)
                    for k in ("entity_id", "channel", "source_order_id", "source_version")
                ):
                    raise BusinessError(
                        "DRAFT_SOURCE_MISMATCH", "不能改变草稿的所属主体或原单", 409
                    )
                repo.execute(
                    """UPDATE med.document_draft SET content=CAST(:content AS jsonb),title=:title,
                    version=version+1,updated_at=clock_timestamp() WHERE id=:id""",
                    {
                        "id": draft_id,
                        "content": body.content.model_dump_json(),
                        "title": body.title,
                    },
                )
            return wire(owned(repo, ctx, draft_id))

        return command(
            self.uow, ctx, "draft.save", str(draft_id), key, body.model_dump(mode="json"), execute
        )

    def discard(
        self, ctx: TenantContext, draft_id: UUID, body: DraftDiscard, key: str
    ) -> dict[str, Any]:
        self.detail(ctx, draft_id)

        def execute(repo: SqlSession) -> dict[str, Any]:
            row = owned(repo, ctx, draft_id, lock=True)
            if row["state"] != "EDITING" or str(row["version"]) != body.expected_version:
                raise BusinessError("DRAFT_VERSION_CONFLICT", "草稿状态已变化，请重新读取", 409)
            repo.execute(
                """UPDATE med.document_draft SET state='DISCARDED',version=version+1,
                updated_at=clock_timestamp() WHERE id=:id""",
                {"id": draft_id},
            )
            audit(repo, ctx, "document_draft", draft_id, "draft.discarded", {})
            return {"id": str(draft_id), "state": "DISCARDED"}

        return command(
            self.uow,
            ctx,
            "draft.discard",
            str(draft_id),
            key,
            body.model_dump(mode="json"),
            execute,
        )
