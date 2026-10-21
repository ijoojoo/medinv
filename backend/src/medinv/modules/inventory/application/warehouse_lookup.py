"""Bounded warehouse choices for purchase/sale entry, without loading locations."""

from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.tenancy.context import TenantContext


class WarehouseLookup:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def search(
        self,
        ctx: TenantContext,
        entity: UUID,
        channel: Literal["purchase", "sale"],
        search: str = "",
        offset: int = 0,
        warehouse_id: UUID | None = None,
    ) -> dict[str, Any]:
        ctx.require("purchase.read" if channel == "purchase" else "sales.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT id,code,name,(archived_at IS NULL) AS eligible
                FROM med.warehouse WHERE entity_id=:entity AND kind='PHYSICAL'
                  AND (CAST(:id AS uuid) IS NULL OR id=:id)
                  AND (CAST(:id AS uuid) IS NOT NULL OR archived_at IS NULL)
                  AND strpos(lower(concat_ws(' ',name,code)),lower(:search))>0
                ORDER BY name,code,id LIMIT 21 OFFSET :offset""",
                {"entity": entity, "id": warehouse_id, "search": search.strip(), "offset": offset},
            )
            return {"items": jsonable_encoder(rows[:20]), "has_more": len(rows) > 20}
