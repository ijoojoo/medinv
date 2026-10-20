"""Atomic quantity movements within an entity; no shared cost-pool mutation."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.application.service import one
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def lock_goods(repo: SqlSession, entity: UUID, lines: list[dict[str, Any]]) -> None:
    skus = sorted({r["sku_id"] for r in lines})
    repo.rows(
        """SELECT s.id FROM med.sku s JOIN med.product p
      ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
      JOIN med.stock_gate g ON (g.tenant_id,g.sku_id)=(s.tenant_id,s.id) AND g.entity_id=:e
      WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p,g""",
        {"e": entity, "ids": skus},
    )
    repo.rows(
        "SELECT id FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
        {"ids": sorted({r["lot_id"] for r in lines})},
    )


def balance(
    repo: SqlSession,
    ctx: TenantContext,
    entity: UUID,
    warehouse: UUID,
    location: UUID,
    line: dict[str, Any],
    quality: str,
) -> dict[str, Any]:
    params = {
        "t": ctx.tenant_id,
        "a": ctx.principal_id,
        "e": entity,
        "w": warehouse,
        "l": location,
        "s": line["sku_id"],
        "lot": line["lot_id"],
        "quality": quality,
    }
    repo.execute(
        """INSERT INTO med.stock_balance(tenant_id,id,created_by,updated_by,
      entity_id,owner_id,warehouse_id,location_id,sku_id,lot_id,quality_status)
      VALUES(:t,:id,:a,:a,:e,:e,:w,:l,:s,:lot,:quality) ON CONFLICT DO NOTHING""",
        {**params, "id": uuid4()},
    )
    return one(
        repo,
        """SELECT * FROM med.stock_balance WHERE entity_id=:e AND owner_id=:e
      AND warehouse_id=:w AND location_id=:l AND sku_id=:s AND lot_id=:lot
      AND quality_status=:quality FOR UPDATE""",
        params,
    )


def move(
    repo: SqlSession,
    ctx: TenantContext,
    pid: UUID,
    line: dict[str, Any],
    qty: Decimal,
    source: dict[str, Any],
    dest: dict[str, Any],
    action: str,
) -> None:
    if qty > source["on_hand"] - source["reserved"] - source["blocked"]:
        raise BusinessError("INSUFFICIENT_STOCK", "库存已变化，来源未占用数量不足", 409)
    if dest["on_hand"] + qty >= Decimal("1e14"):
        raise BusinessError("BALANCE_OVERFLOW", "目的库存超过数量上限", 422)
    pl = uuid4()
    repo.execute(
        """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,source_line_id,
      line_no,sku_id,base_qty,product_snapshot,package_snapshot)
      VALUES(:t,:id,:a,:p,:line,:n,:s,:q,CAST(:product AS jsonb),CAST(:package AS jsonb))""",
        {
            "t": ctx.tenant_id,
            "id": pl,
            "a": ctx.principal_id,
            "p": pid,
            "line": line["id"],
            "n": line["line_no"],
            "s": line["sku_id"],
            "q": qty,
            "product": json.dumps(line["product_snapshot"]),
            "package": json.dumps(line["package_snapshot"]),
        },
    )
    # Two quantity effects in one posting. The shared entity/lot cost pool is unchanged.
    for index, (row, delta) in enumerate(((source, -qty), (dest, qty))):
        repo.execute(
            """UPDATE med.stock_balance SET on_hand=on_hand+:q,version=version+1,
          updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"id": row["id"], "q": delta, "a": ctx.principal_id},
        )
        repo.execute(
            """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
          effect_index,balance_id,qty_delta,event_type,posted_at)
          VALUES(:t,:id,:a,:pl,:i,:b,:q,:action,clock_timestamp())""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "a": ctx.principal_id,
                "pl": pl,
                "i": index,
                "b": row["id"],
                "q": delta,
                "action": action,
            },
        )
        row["on_hand"] += delta
