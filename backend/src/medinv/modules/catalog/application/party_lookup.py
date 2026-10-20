from typing import Any, Literal
from uuid import UUID

from medinv.modules.procurement.application.service import encode
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.tenancy.context import TenantContext


class PartyLookup:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def search(
        self,
        ctx: TenantContext,
        entity: UUID,
        channel: Literal["purchase", "sale"],
        search: str = "",
        offset: int = 0,
        party_id: UUID | None = None,
        qualified_only: bool = True,
    ) -> dict[str, Any]:
        ctx.require("purchase.read" if channel == "purchase" else "sales.read", entity)
        eligible = """state='APPROVED' AND
            (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
            BETWEEN valid_from AND valid_until"""
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                f"""SELECT id,code,name,credit_code,kind,state,valid_from,valid_until,
                  ({eligible}) AS eligible FROM med.party
                WHERE entity_id=:entity AND kind IN (:kind,'BOTH')
                  AND (CAST(:id AS uuid) IS NULL OR id=:id)
                  AND (CAST(:id AS uuid) IS NOT NULL OR NOT :qualified OR ({eligible}))
                  AND strpos(lower(concat_ws(' ',name,code,credit_code,license_no)),
                    lower(:search))>0
                ORDER BY name,id LIMIT 21 OFFSET :offset""",
                {
                    "entity": entity,
                    "kind": "SUPPLIER" if channel == "purchase" else "CUSTOMER",
                    "id": party_id,
                    "qualified": qualified_only,
                    "search": search.strip(),
                    "offset": offset,
                },
            )
            return {"items": encode(rows[:20]), "has_more": len(rows) > 20}
