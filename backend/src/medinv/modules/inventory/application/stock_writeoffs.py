"""Shared owned-stock loss posting for physical and transit balances."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.domain.decimal_values import portion
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def loss_cost(pool: dict[str, Any], qty: Decimal) -> Decimal:
    if qty <= 0 or qty > pool["qty"]:
        raise BusinessError("COST_POOL_INSUFFICIENT", "批次成本数量不足，不能核销", 409)
    return portion(pool["amount"], Decimal(0), qty, pool["qty"], ".00000001")


def write_off(
    repo: SqlSession,
    ctx: TenantContext,
    pid: UUID,
    line: dict[str, Any],
    qty: Decimal,
    row: dict[str, Any],
    pool: dict[str, Any],
    day: Any,
    action: str,
) -> None:
    if qty > row["on_hand"] - row["reserved"] - row["blocked"]:
        raise BusinessError("INSUFFICIENT_STOCK", "当前未占用数量不足，不能核销", 409)
    pl = uuid4()
    params = {
        "t": ctx.tenant_id,
        "a": ctx.principal_id,
        "pl": pl,
        "p": pid,
        "source": line["id"],
        "s": row["sku_id"],
        "q": qty,
        "b": row["id"],
        "pool": pool["id"],
        "cost": loss_cost(pool, qty),
        "action": action,
        "n": line["line_no"],
        "day": day,
        "product": json.dumps(line["product_snapshot"]),
        "package": json.dumps(line["package_snapshot"]),
    }
    repo.execute(
        """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,
      source_line_id,line_no,sku_id,base_qty,product_snapshot,package_snapshot)
      VALUES(:t,:pl,:a,:p,:source,:n,:s,:q,CAST(:product AS jsonb),
        CAST(:package AS jsonb))""",
        params,
    )
    repo.execute(
        """UPDATE med.stock_balance SET on_hand=on_hand-:q,version=version+1,
      updated_by=:a,updated_at=clock_timestamp() WHERE id=:b""",
        params,
    )
    repo.execute(
        """UPDATE med.cost_balance SET qty=qty-:q,amount=amount-:cost,
      version=version+1,updated_by=:a,updated_at=clock_timestamp() WHERE id=:pool""",
        params,
    )
    repo.execute(
        """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
      effect_index,balance_id,qty_delta,event_type,posted_at)
      VALUES(:t,:id,:a,:pl,0,:b,-:q,:action,clock_timestamp())""",
        {**params, "id": uuid4()},
    )
    repo.execute(
        """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
      effect_index,cost_balance_id,qty_delta,amount_delta,business_date)
      VALUES(:t,:id,:a,:pl,0,:pool,-:q,-:cost,:day)""",
        {**params, "id": uuid4()},
    )
