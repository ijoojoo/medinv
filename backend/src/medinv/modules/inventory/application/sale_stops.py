"""Quality sale-stop commands share the existing transactional product eligibility gate."""

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, StrictBool

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.catalog.application.entity_settings import FIELDS
from medinv.modules.catalog.application.master import raw_record
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class SaleStopSave(DTO):
    entity_id: UUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    stopped: StrictBool
    reason: Text


def save(
    uow: TenantUnitOfWork, ctx: TenantContext, product: UUID, body: SaleStopSave, key: str
) -> dict[str, Any]:
    ctx.require("quality.review", body.entity_id)

    def run(repo: SqlSession) -> dict[str, Any]:
        master = raw_record(repo, product, lock=True)
        if not repo.rows(
            "SELECT id FROM med.legal_entity WHERE id=:e AND status='ACTIVE' FOR SHARE",
            {"e": body.entity_id},
        ):
            raise BusinessError("NOT_FOUND", "经营主体不存在或已停用", 404)
        rows = repo.rows(
            "SELECT * FROM med.product_entity_settings WHERE product_id=:p AND "
            "entity_id=:e FOR UPDATE",
            {"p": product, "e": body.entity_id},
        )
        version = rows[0]["version"] if rows else 0
        if body.expected_version != version:
            raise BusinessError("VERSION_CONFLICT", "商品经营状态已变化，请重新读取", 409)
        before = (
            {k: rows[0][k] for k in FIELDS}
            if rows
            else {
                "enabled": True,
                "purchase_enabled": True,
                "sales_enabled": True,
                **{
                    k: master[k]
                    for k in FIELDS
                    if k not in {"enabled", "purchase_enabled", "sales_enabled"}
                },
            }
        )
        before["quality_stopped"] = rows[0]["quality_stopped"] if rows else False
        if before["quality_stopped"] == body.stopped:
            raise BusinessError("UNCHANGED", "商品停售状态没有变化", 422)
        after = {**before, "quality_stopped": body.stopped}
        result = repo.rows(
            "INSERT INTO med.product_entity_settings(tenant_id,product_id,entity_id,"
            "updated_by,quality_stopped,"
            + ",".join(FIELDS)
            + ") VALUES(:t,:p,:e,:a,:quality_stopped,"
            + ",".join(":" + k for k in FIELDS)
            + ") "
            "ON CONFLICT(tenant_id,product_id,entity_id) DO UPDATE SET "
            "quality_stopped=EXCLUDED.quality_stopped,"
            "version=product_entity_settings.version+1,updated_by=:a,"
            "updated_at=clock_timestamp() RETURNING version",
            {**after, "t": ctx.tenant_id, "p": product, "e": body.entity_id, "a": ctx.principal_id},
        )[0]
        audit(
            repo,
            ctx,
            "product",
            product,
            "quality.sale_stopped" if body.stopped else "quality.sale_resumed",
            {
                "entity_id": str(body.entity_id),
                "before": before,
                "after": after,
                "reason": body.reason,
                "version": result["version"],
            },
        )
        return {"version": result["version"], "stopped": body.stopped}

    return command(
        uow, ctx, "quality.sale_stop", str(product), key, body.model_dump(mode="json"), run
    )
