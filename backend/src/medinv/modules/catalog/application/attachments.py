import unicodedata
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.application.maintenance import CatalogMaintenance, Kind
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

MAX_BYTES = 5 * 1024 * 1024
MAX_PARENT_BYTES = 100 * 1024 * 1024
METADATA = (
    "id,root_id,previous_id,version,filename,media_type,byte_size,"
    "sha256,reason,created_by,created_at"
)


def validate_file(filename: str, content: bytes) -> str:
    if not 1 <= len(content) <= MAX_BYTES:
        raise BusinessError("ATTACHMENT_SIZE", "单份附件须为 1 字节至 5 MiB", 413)
    if (
        not filename.strip()
        or filename != filename.strip()
        or len(filename) > 180
        or any(c in filename for c in '/\\:<>"|?*')
        or any(unicodedata.category(c).startswith("C") for c in filename)
    ):
        raise BusinessError("ATTACHMENT_NAME", "附件名称不能包含路径或控制字符", 422)
    suffix = filename.rsplit(".", 1)[-1].lower()
    # Format identification, not malware scanning. PDFs are always downloaded.
    if suffix == "pdf" and content.startswith(b"%PDF-") and b"%%EOF" in content[-1024:]:
        return "application/pdf"
    if (
        suffix == "png"
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content.endswith(b"IEND\xaeB`\x82")
    ):
        return "image/png"
    if (
        suffix in {"jpg", "jpeg"}
        and content.startswith(b"\xff\xd8\xff")
        and content.endswith(b"\xff\xd9")
    ):
        return "image/jpeg"
    raise BusinessError("ATTACHMENT_TYPE", "仅支持文件格式与扩展名一致的 PDF、PNG、JPEG", 415)


def attachment_record(
    repo: SqlSession, kind: Kind, parent: UUID, target: UUID, *, content: bool = False
) -> dict[str, Any]:
    rows = repo.rows(
        f"SELECT {METADATA}{',content' if content else ''} FROM med.catalog_attachment "
        f"WHERE {kind}_id=:parent AND id=:id",
        {"parent": parent, "id": target},
    )
    if not rows:
        raise BusinessError("ATTACHMENT_NOT_FOUND", "附件不可见或不属于当前资料", 404)
    return rows[0]


class CatalogAttachments:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def scope(self, ctx: TenantContext, kind: Kind, parent: UUID, permission: str) -> None:
        CatalogMaintenance(self.uow).scope(ctx, kind, parent, permission)

    def listing(self, ctx: TenantContext, kind: Kind, parent: UUID) -> dict[str, Any]:
        self.scope(ctx, kind, parent, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"SELECT {METADATA} FROM med.catalog_attachment WHERE {kind}_id=:parent "
                "ORDER BY created_at DESC,id LIMIT 100",
                {"parent": parent},
            )
            return dict(jsonable_encoder({"items": rows, "max_bytes": MAX_BYTES}))

    def download(
        self, ctx: TenantContext, kind: Kind, parent: UUID, target: UUID
    ) -> tuple[dict[str, Any], bytes]:
        self.scope(ctx, kind, parent, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            row = attachment_record(SqlSession(session), kind, parent, target, content=True)
            content = bytes(row.pop("content"))
            if len(content) != row["byte_size"] or sha256(content).hexdigest() != row["sha256"]:
                raise BusinessError("ATTACHMENT_INTEGRITY", "附件完整性校验失败，请联系管理员", 503)
            return row, content

    def upload(
        self,
        ctx: TenantContext,
        kind: Kind,
        parent: UUID,
        filename: str,
        content: bytes,
        reason: str,
        key: str,
        previous: UUID | None = None,
    ) -> dict[str, Any]:
        self.scope(ctx, kind, parent, "catalog.write")
        media_type = validate_file(filename, content)
        if not 1 <= len(reason.strip()) <= 500:
            raise BusinessError("ATTACHMENT_REASON", "请填写 1 至 500 字的上传说明", 422)
        digest = sha256(content).hexdigest()

        def run(repo: SqlSession) -> dict[str, Any]:
            row = CatalogMaintenance.record(repo, kind, parent, lock=True)
            if kind == "party":
                ctx.require("catalog.write", row["entity_id"])
            usage = repo.rows(
                f"SELECT count(*) AS count,coalesce(sum(byte_size),0) AS bytes "
                f"FROM med.catalog_attachment WHERE {kind}_id=:parent",
                {"parent": parent},
            )[0]
            if usage["count"] >= 100 or usage["bytes"] + len(content) > MAX_PARENT_BYTES:
                raise BusinessError(
                    "ATTACHMENT_LIMIT", "每份资料含历史最多 100 个附件版本、100 MiB", 422
                )
            target = uuid4()
            prior = attachment_record(repo, kind, parent, previous) if previous else None
            if prior and repo.rows(
                "SELECT id FROM med.catalog_attachment WHERE previous_id=:previous",
                {"previous": previous},
            ):
                raise BusinessError(
                    "VERSION_CONFLICT", "此附件已有更新版本，请刷新后选择最新版本", 409
                )
            params = {
                "tenant": ctx.tenant_id,
                "id": target,
                "parent": parent,
                "root": prior["root_id"] if prior else target,
                "previous": previous,
                "version": prior["version"] + 1 if prior else 1,
                "filename": filename,
                "media_type": media_type,
                "size": len(content),
                "sha256": digest,
                "content": content,
                "reason": reason.strip(),
                "actor": ctx.principal_id,
            }
            repo.execute(
                f"""INSERT INTO med.catalog_attachment
                (tenant_id,id,{kind}_id,root_id,previous_id,version,filename,media_type,
                 byte_size,sha256,content,reason,created_by)
                VALUES(:tenant,:id,:parent,:root,:previous,:version,:filename,:media_type,
                 :size,:sha256,:content,:reason,:actor)""",
                params,
            )
            result = dict(jsonable_encoder(attachment_record(repo, kind, parent, target)))
            audit(repo, ctx, kind, parent, "catalog.attachment_uploaded", result)
            return result

        return command(
            self.uow,
            ctx,
            "catalog.attachment.upload",
            f"{kind}:{parent}",
            key,
            {
                "filename": filename,
                "sha256": digest,
                "byte_size": len(content),
                "reason": reason,
                "previous_id": str(previous) if previous else None,
            },
            run,
        )
