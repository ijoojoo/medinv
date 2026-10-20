"""Return rejected, unowned arrivals to the supplier without inventory/financial posting."""

from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.domain.decimal_values import quantity
from medinv.modules.procurement.api.schemas import RejectedHandover
from medinv.modules.procurement.application.service import ProcurementService, one, version
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def hand_back(
    service: ProcurementService,
    ctx: TenantContext,
    receipt_id: UUID,
    body: RejectedHandover,
    key: str,
) -> dict[str, Any]:
    ctx.require("purchase.receive")
    order_id = service.receipt_order(ctx, receipt_id)
    service.scope(ctx, "purchase.receive", order_id)

    def execute(repo: SqlSession) -> dict[str, Any]:
        # Physical handback remains possible after closure, expiry or supplier suspension.
        _, _, skus = service.locked_order(repo, ctx, order_id, "purchase.receive", validate=False)
        receipt = one(repo, "SELECT * FROM med.receipt WHERE id=:id FOR UPDATE", {"id": receipt_id})
        version(receipt, body.expected_version)
        hid = uuid4()
        repo.execute(
            """INSERT INTO med.purchase_rejected_handover
            (tenant_id,id,receipt_id,handover_ref,recipient,reason,created_by)
            VALUES(:t,:id,:r,:ref,:recipient,:reason,:a)""",
            {
                "t": ctx.tenant_id,
                "id": hid,
                "r": receipt_id,
                "ref": body.handover_ref,
                "recipient": body.recipient,
                "reason": body.reason,
                "a": ctx.principal_id,
            },
        )
        for line in body.lines:
            row = one(
                repo,
                "SELECT * FROM med.receipt_line WHERE id=:id AND receipt_id=:r FOR UPDATE",
                {"id": line.receipt_line_id, "r": receipt_id},
            )
            try:
                qty = quantity(line.qty, sku_scale=skus[row["sku_id"]]["qty_scale"])
            except ValueError as exc:
                raise BusinessError("INVALID_QUANTITY", "退回数量不符合商品精度", 422) from exc
            if qty > row["rejected_qty"] - row["rejected_returned_qty"]:
                raise BusinessError(
                    "REJECTED_QTY_EXCEEDED", "退回数量超过已拒收且尚未退回的数量", 409
                )
            repo.execute(
                """INSERT INTO med.purchase_rejected_handover_line
                (tenant_id,id,handover_id,receipt_id,receipt_line_id,qty)
                VALUES(:t,:id,:h,:r,:line,:qty)""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "h": hid,
                    "r": receipt_id,
                    "line": row["id"],
                    "qty": qty,
                },
            )
            repo.execute(
                """UPDATE med.receipt_line SET rejected_returned_qty=rejected_returned_qty+:qty
                WHERE id=:id""",
                {"id": row["id"], "qty": qty},
            )
        repo.execute("UPDATE med.receipt SET version=version+1 WHERE id=:id", {"id": receipt_id})
        audit(
            repo,
            ctx,
            "receipt",
            receipt_id,
            "purchase.rejected_handed_back",
            {"handover_id": str(hid), **body.model_dump(mode="json")},
        )
        return {
            "id": str(hid),
            "receipt_id": str(receipt_id),
            "version": str(receipt["version"] + 1),
        }

    return service.run(ctx, "purchase.rejected_hand_back", receipt_id, body, key, execute)
