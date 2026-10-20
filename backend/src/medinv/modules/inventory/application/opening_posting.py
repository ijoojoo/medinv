import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.application.openings import OpeningService
from medinv.modules.inventory.application.stock_movements import balance
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import one, version
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def post(
    service: OpeningService, ctx: TenantContext, oid: UUID, body: Transition, key: str
) -> dict[str, Any]:
    entity = service.scope(ctx, oid, "opening.post")

    def run(repo: SqlSession) -> dict[str, Any]:
        service.finance.entity(repo, ctx, entity, "opening.post")
        doc = one(repo, "SELECT * FROM med.inventory_opening WHERE id=:id FOR UPDATE", {"id": oid})
        if doc["state"] == "POSTED":
            return dict(
                one(
                    repo,
                    "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
                    {"id": doc["posting_id"]},
                )["result_snapshot"]
            )
        version(doc, body.expected_version)
        if doc["state"] != "APPROVED":
            raise BusinessError("OPENING_NOT_APPROVED", "期初清单必须先完成独立审核", 409)
        if doc["created_by"] == ctx.principal_id:
            raise BusinessError("MAKER_CHECKER", "期初制单人不能启用本单", 403)
        service.unused(repo, entity, doc["warehouse_id"])
        lines = service.lines(repo, oid)
        goods = service.goods(repo, ctx, entity, lines)
        day = service.finance.today(repo)
        service.finance.period(repo, ctx, entity, day)
        pid = uuid4()
        result = {
            "id": str(oid),
            "posting_id": str(pid),
            "state": "POSTED",
            "version": str(doc["version"] + 1),
            "business_date": day.isoformat(),
            "line_count": len(lines),
            "cost_total": format(sum((line["cost_amount"] for line in lines), Decimal(0)), ".8f"),
        }
        repo.execute(
            """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
          source_id,action,segment_id,business_date,posted_at,request_id,result_snapshot)
          VALUES(:t,:p,:a,:e,'INVENTORY_OPENING',:id,'OPENING_BALANCE',:id,:day,clock_timestamp(),:req,CAST(:result
          AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "p": pid,
                "a": ctx.principal_id,
                "e": entity,
                "id": oid,
                "day": day,
                "req": ctx.request_id,
                "result": json.dumps(result),
            },
        )
        for line in lines:
            sku = goods[line["sku_id"]]
            line["lot_id"] = service.lot(repo, ctx, line, sku)
            stock = balance(
                repo, ctx, entity, doc["warehouse_id"], line["location_id"], line, "QUALIFIED"
            )
            params = {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "a": ctx.principal_id,
                "e": entity,
                "s": line["sku_id"],
                "lot": line["lot_id"],
            }
            repo.execute(
                """INSERT INTO
med.cost_balance(tenant_id,id,created_by,updated_by,entity_id,owner_id,sku_id,lot_id,qty,amount)
              VALUES(:t,:id,:a,:a,:e,:e,:s,:lot,0,0) ON CONFLICT DO NOTHING""",
                params,
            )
            pool = one(
                repo,
                """SELECT * FROM med.cost_balance WHERE entity_id=:e AND owner_id=:e AND sku_id=:s
                AND lot_id=:lot FOR UPDATE""",
                params,
            )
            if (
                stock["on_hand"] + line["qty"] >= Decimal("1e14")
                or pool["qty"] + line["qty"] >= Decimal("1e14")
                or pool["amount"] + line["cost_amount"] >= Decimal("1e16")
            ):
                raise BusinessError("BALANCE_OVERFLOW", "期初库存或成本池超过上限", 422)
            params.update(
                {
                    "pl": uuid4(),
                    "p": pid,
                    "source": line["id"],
                    "n": line["line_no"],
                    "qty": line["qty"],
                    "cost": line["cost_amount"],
                    "b": stock["id"],
                    "pool": pool["id"],
                    "day": day,
                    "product": json.dumps(line["product_snapshot"]),
                    "package": json.dumps(line["package_snapshot"]),
                }
            )
            repo.execute(
                """INSERT INTO med.posting_line
                (tenant_id,id,created_by,posting_id,source_line_id,line_no,sku_id,
              base_qty,product_snapshot,package_snapshot)
              VALUES(:t,:pl,:a,:p,:source,:n,:s,:qty,CAST(:product AS jsonb),CAST(:package
              AS jsonb))""",
                params,
            )
            repo.execute(
                """UPDATE med.stock_balance SET
                on_hand=on_hand+:qty,version=version+1,updated_by=:a,updated_at=clock_timestamp()
                WHERE id=:b""",
                params,
            )
            repo.execute(
                """UPDATE med.cost_balance SET
                qty=qty+:qty,amount=amount+:cost,version=version+1,updated_by=:a,updated_at=clock_timestamp()
                WHERE id=:pool""",
                params,
            )
            repo.execute(
                """INSERT INTO
med.stock_ledger(tenant_id,id,created_by,posting_line_id,effect_index,balance_id,qty_delta,event_type,posted_at)
              VALUES(:t,:id,:a,:pl,0,:b,:qty,'OPENING_BALANCE',clock_timestamp())""",
                {**params, "id": uuid4()},
            )
            repo.execute(
                """INSERT INTO
med.cost_ledger(tenant_id,id,created_by,posting_line_id,effect_index,cost_balance_id,qty_delta,amount_delta,business_date)
              VALUES(:t,:id,:a,:pl,0,:pool,:qty,:cost,:day)""",
                {**params, "id": uuid4()},
            )
        repo.execute(
            """UPDATE med.inventory_opening SET state='POSTED',posting_id=:p,version=version+1
            WHERE id=:id""",
            {"id": oid, "p": pid},
        )
        audit(
            repo,
            ctx,
            "inventory_opening",
            oid,
            "inventory.opening_posted",
            {**result, "reason": body.reason},
        )
        event(
            repo,
            ctx,
            uuid4(),
            "inventory_opening",
            oid,
            doc["version"] + 1,
            "inventory.opening_posted",
            result,
        )
        return result

    return command(
        service.uow, ctx, "opening.post", str(oid), key, body.model_dump(mode="json"), run
    )
