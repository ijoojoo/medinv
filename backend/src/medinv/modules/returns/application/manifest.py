"""Immutable return quantities and cumulative allocation against original line snapshots."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from medinv.modules.inventory.domain.decimal_values import portion, quantity
from medinv.modules.returns.api.schemas import ReturnCreate
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError


def remaining(repo: SqlSession, pid: UUID, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history = repo.rows(
        """SELECT rl.source_posting_line_id,sum(rl.qty) AS qty
        FROM med.return_line rl JOIN med.return_document r
          ON (r.tenant_id,r.id)=(rl.tenant_id,rl.return_id)
        WHERE r.source_posting_id=:p AND r.state='POSTED'
        GROUP BY rl.source_posting_line_id""",
        {"p": pid},
    )
    totals = {r["source_posting_line_id"]: r["qty"] for r in history}
    legacy = bool(
        repo.rows(
            """SELECT id FROM med.return_document WHERE source_posting_id=:p
        AND state='POSTED' AND workflow_version=1""",
            {"p": pid},
        )
    )
    return [
        {
            **line,
            "returned_qty": line["base_qty"] if legacy else totals.get(line["id"], Decimal(0)),
            "remaining_qty": Decimal(0) if legacy else line["base_qty"] - totals.get(line["id"], 0),
        }
        for line in lines
    ]


def prepare(
    source: dict[str, Any], lines: list[dict[str, Any]], body: ReturnCreate
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    requested = (
        {r.source_posting_line_id: Decimal(r.qty) for r in body.lines}
        if body.lines is not None
        else {r["id"]: r["remaining_qty"] for r in lines if r["remaining_qty"] > 0}
    )
    if not requested:
        raise BusinessError("RETURN_EXISTS", "原批次已全部退回", 409)
    if set(requested) - {r["id"] for r in lines}:
        raise BusinessError("RETURN_LINE_INVALID", "退货明细不属于原过账批次", 422)
    supported = all(r["gross_amount"] is not None for r in lines)
    if not supported and requested != {r["id"]: r["base_qty"] for r in lines}:
        raise BusinessError("RETURN_LEGACY_FULL_ONLY", "历史原单缺少逐行金额，只支持整批退货", 409)
    if supported and (
        sum(r["gross_amount"] for r in lines) != source["gross_amount"]
        or sum(r["net_amount"] for r in lines) != source["net_amount"]
    ):
        raise BusinessError("RETURN_SOURCE_UNSUPPORTED", "原行金额与往来凭据不一致", 409)
    prepared = []
    for r in lines:
        if r["id"] not in requested:
            continue
        qty = requested[r["id"]]
        try:
            quantity(format(qty, "f"), sku_scale=int(r["package_snapshot"]["qty_scale"]))
        except (ValueError, KeyError) as exc:
            raise BusinessError("RETURN_QUANTITY_INVALID", "数量不符合原商品精度", 422) from exc
        if qty > r["remaining_qty"]:
            raise BusinessError("RETURN_QUANTITY_EXCEEDED", "本次数量超过原行剩余可退数量", 409)
        start, end = Decimal(r["returned_qty"]), Decimal(r["returned_qty"]) + qty
        gross = net = None
        if supported:
            gross = portion(r["gross_amount"], start, end, r["base_qty"])
            old_g = portion(r["gross_amount"], Decimal(0), start, r["base_qty"])
            net = (
                portion(r["net_amount"], old_g, old_g + gross, r["gross_amount"])
                if r["gross_amount"]
                else Decimal(0)
            )
        cost = portion(abs(r["amount_delta"]), start, end, r["base_qty"], ".00000001")
        prepared.append(
            {**r, "base_qty": qty, "gross_amount": gross, "net_amount": net, "original_cost": cost}
        )
    gross_total = (
        sum((r["gross_amount"] for r in prepared), Decimal(0))
        if supported
        else source["gross_amount"]
    )
    net_total = (
        sum((r["net_amount"] for r in prepared), Decimal(0)) if supported else source["net_amount"]
    )
    return prepared, gross_total, net_total


def selected(
    repo: SqlSession, doc: dict[str, Any], original: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if doc["workflow_version"] == 1:
        return [
            {**r, "source_qty": r["base_qty"], "original_cost": abs(r["amount_delta"])}
            for r in original
        ]
    rows = repo.rows("SELECT * FROM med.return_line WHERE return_id=:id", {"id": doc["id"]})
    amounts = {r["source_posting_line_id"]: r for r in rows}
    if not rows or set(amounts) - {r["id"] for r in original}:
        raise BusinessError("RETURN_SOURCE_UNSUPPORTED", "退货明细缺失或不属于原单", 409)
    return [
        {
            **r,
            "source_qty": r["base_qty"],
            "base_qty": amounts[r["id"]]["qty"],
            **{k: amounts[r["id"]][k] for k in ("gross_amount", "net_amount", "original_cost")},
        }
        for r in original
        if r["id"] in amounts
    ]
