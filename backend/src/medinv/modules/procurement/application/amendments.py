import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.procurement.api.schemas import OrderRevision
from medinv.modules.procurement.application.document_projection import public_documents
from medinv.modules.workspace.drafts import finish_draft
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

if TYPE_CHECKING:
    from medinv.modules.procurement.application.service import ProcurementService


def snapshot(repo: SqlSession, target: UUID) -> dict[str, Any]:
    header = repo.rows(
        """SELECT o.id,o.number,o.entity_id,o.supplier_id,p.name AS supplier_name,
        o.warehouse_id,w.name AS warehouse_name,o.approval_state,o.approved_by,o.approved_at,
        o.revision_no,o.version FROM med.purchase_order o
        JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.supplier_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
        WHERE o.id=:id""",
        {"id": target},
    )[0]
    header["version"] = str(header["version"])
    lines = repo.rows(
        """SELECT l.id,l.line_no,l.sku_id,p.name,s.code,s.base_unit,l.qty,l.unit_price,
        l.tax_rate,l.trade_qty,l.package_snapshot,l.gross_amount,l.net_amount,l.document_snapshot
        FROM med.purchase_order_line l
        JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
        JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
        WHERE l.order_id=:id ORDER BY l.line_no""",
        {"id": target},
    )
    for line in lines:
        values = (line.get("document_snapshot") or {}).get("values", {})
        if values:
            for key, stored in (("name", "product"), ("code", "code"), ("base_unit", "base_unit")):
                line[key] = values.get(stored) or "历史未留存"
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {"order": header, "lines": lines}, custom_encoder={Decimal: lambda d: format(d, "f")}
        ),
    )


def history(
    svc: ProcurementService, ctx: TenantContext, target: UUID, offset: int
) -> dict[str, Any]:
    svc.scope(ctx, "purchase.read", target)
    with svc.uow.open(ctx, readonly=True) as session:
        rows = SqlSession(session).rows(
            """SELECT id,created_by,created_at,action,reason FROM med.audit_event
            WHERE resource_type='purchase_order' AND resource_id=:id
            ORDER BY created_at DESC,id LIMIT 51 OFFSET :offset""",
            {"id": target, "offset": offset},
        )
        return dict(
            jsonable_encoder(
                {
                    "items": [
                        {
                            **{k: r[k] for k in ("id", "created_by", "created_at", "action")},
                            "detail": public_documents(json.loads(r["reason"])),
                        }
                        for r in rows[:50]
                    ],
                    "has_more": len(rows) > 50,
                }
            )
        )


def revise(
    svc: ProcurementService, ctx: TenantContext, target: UUID, body: OrderRevision, key: str
) -> dict[str, Any]:
    svc.scope(ctx, "purchase.write", target)

    def execute(repo: SqlSession) -> dict[str, Any]:
        order, lines, _ = svc.locked_order(repo, ctx, target, "purchase.write", validate=False)
        if str(order["version"]) != body.expected_version:
            raise BusinessError("VERSION_CONFLICT", "采购单已变化，请刷新后修改", 409)
        if (
            order["approval_state"] not in {"DRAFT", "APPROVED"}
            or order["fulfillment_state"] != "OPEN"
        ):
            raise BusinessError("INVALID_STATE", "仅未关闭的待审核或已审核采购单可修改", 409)
        if any(row["received_qty"] or row["closed_qty"] for row in lines) or repo.rows(
            "SELECT id FROM med.receipt WHERE order_id=:id LIMIT 1", {"id": target}
        ):
            raise BusinessError("PURCHASE_ALREADY_RECEIVED", "已登记到货，不能修改原采购约定", 409)
        unchanged = (
            body.supplier_id == order["supplier_id"]
            and body.warehouse_id == order["warehouse_id"]
            and len(body.lines) == len(lines)
            and all(
                item.sku_id == old["sku_id"]
                and str(item.packaging_id or "")
                == str((old["package_snapshot"] or {}).get("packaging_id", ""))
                and Decimal(item.qty)
                == (old["trade_qty"] if old["trade_qty"] is not None else old["qty"])
                and Decimal(item.unit_price) == old["unit_price"]
                and Decimal(item.tax_rate) == old["tax_rate"]
                for item, old in zip(body.lines, lines, strict=True)
            )
        )
        if unchanged:
            raise BusinessError("PURCHASE_UNCHANGED", "采购内容没有变化", 422)
        supplier = svc.supplier(repo, order["entity_id"], body.supplier_id)
        if not repo.rows(
            """SELECT id FROM med.warehouse WHERE id=:w AND entity_id=:e
            AND kind='PHYSICAL' AND archived_at IS NULL FOR SHARE""",
            {"w": body.warehouse_id, "e": order["entity_id"]},
        ):
            raise BusinessError("WAREHOUSE_UNAVAILABLE", "收货仓库不可用或不属于该主体", 422)
        skus = svc.products(
            repo, ctx, order["entity_id"], [item.sku_id for item in body.lines], supplier
        )
        before = snapshot(repo, target)
        editors = set() if order["approval_state"] == "APPROVED" else set(order["revision_editors"])
        editors.add(ctx.principal_id)
        repo.execute(
            """UPDATE med.purchase_order SET supplier_id=:s,warehouse_id=:w,
            approval_state='DRAFT',approved_by=NULL,approved_at=NULL,
            revision_no=revision_no+1,revision_editors=:editors,version=version+1 WHERE id=:id""",
            {
                "id": target,
                "s": body.supplier_id,
                "w": body.warehouse_id,
                "editors": sorted(editors),
            },
        )
        repo.execute("DELETE FROM med.purchase_order_line WHERE order_id=:id", {"id": target})
        svc.insert_lines(repo, ctx, target, body.lines, skus)
        after = snapshot(repo, target)
        result = {
            "id": str(target),
            "version": str(order["version"] + 1),
            "revision_no": order["revision_no"] + 1,
            "state": "DRAFT",
        }
        audit(
            repo,
            ctx,
            "purchase_order",
            target,
            "purchase.revised",
            {"before": before, "after": after, "reason": body.reason, **result},
        )
        finish_draft(
            repo,
            ctx,
            body.editor_draft,
            "purchase",
            order["entity_id"],
            target,
            target,
            body.expected_version,
        )
        return result

    return svc.run(ctx, "purchase.revise", target, body, key, execute)
