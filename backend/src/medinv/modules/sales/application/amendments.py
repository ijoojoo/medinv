import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from medinv.modules.procurement.application.document_projection import public_documents
from medinv.modules.procurement.application.service import encode, one, version
from medinv.modules.sales.api.schemas import SalesRevision
from medinv.modules.workspace.drafts import finish_draft
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

if TYPE_CHECKING:
    from medinv.modules.sales.application.service import SalesService


def snapshot(repo: SqlSession, oid: UUID) -> dict[str, Any]:
    header = one(
        repo,
        """SELECT o.*,p.name AS customer_name,w.name AS warehouse_name FROM med.sales_order o
        JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.customer_id)
        JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id) WHERE o.id=:id""",
        {"id": oid},
    )
    header["version"] = str(header["version"])
    lines = repo.rows(
        """SELECT l.*,p.name,s.code,s.base_unit FROM med.sales_order_line l
        JOIN med.sku s ON (s.tenant_id,s.id)=(l.tenant_id,l.sku_id)
        JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
        WHERE l.order_id=:id ORDER BY l.line_no""",
        {"id": oid},
    )
    return dict(encode({"order": header, "lines": lines}))


def history(svc: SalesService, ctx: TenantContext, oid: UUID, offset: int) -> dict[str, Any]:
    svc.scope(ctx, "sales.read", oid)
    with svc.uow.open(ctx, readonly=True) as session:
        rows = SqlSession(session).rows(
            """SELECT id,created_by,created_at,action,reason FROM med.audit_event
            WHERE resource_type='sales_order' AND resource_id=:id
            ORDER BY created_at DESC,id LIMIT 51 OFFSET :offset""",
            {"id": oid, "offset": offset},
        )
        return dict(
            encode(
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
    svc: SalesService, ctx: TenantContext, oid: UUID, body: SalesRevision, key: str
) -> dict[str, Any]:
    svc.scope(ctx, "sales.write", oid)

    def execute(repo: SqlSession) -> dict[str, Any]:
        order, lines, _ = svc.locked_order(repo, ctx, oid, "sales.write", validate=False)
        version(order, body.expected_version)
        if order["state"] not in {"DRAFT", "APPROVED"}:
            raise BusinessError("INVALID_STATE", "仅待审核或未分配发货的已审核销售单可修改", 409)
        # Lock the demand before checking all reservation history, including cancelled records.
        repo.rows("SELECT id FROM med.inventory_demand WHERE id=:id FOR UPDATE", {"id": oid})
        if repo.rows(
            "SELECT id FROM med.shipment WHERE order_id=:id LIMIT 1", {"id": oid}
        ) or repo.rows("SELECT id FROM med.reservation WHERE source_id=:id LIMIT 1", {"id": oid}):
            raise BusinessError(
                "SALES_ALREADY_STARTED", "已有分配或发货记录，不能修改原销售约定", 409
            )
        if repo.rows(
            """SELECT id FROM med.inventory_demand_line WHERE demand_id=:id
            AND (reserved_qty<>0 OR fulfilled_qty<>0) LIMIT 1""",
            {"id": oid},
        ):
            raise BusinessError("SALES_ALREADY_STARTED", "销售需求已有占用或履约，不能修改", 409)
        unchanged = (
            all(
                getattr(body, k) == order[k]
                for k in (
                    "customer_id",
                    "warehouse_id",
                    "delivery_address",
                    "minimum_shelf_days",
                    "credit_days",
                )
            )
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
            raise BusinessError("SALES_UNCHANGED", "销售内容没有变化", 422)
        customer = svc.customer(repo, order["entity_id"], body.customer_id)
        one(
            repo,
            "SELECT id FROM med.warehouse WHERE id=:w AND entity_id=:e "
            "AND kind='PHYSICAL' AND archived_at IS NULL FOR SHARE",
            {"w": body.warehouse_id, "e": order["entity_id"]},
        )
        skus = svc.products(
            repo, order["entity_id"], [line.sku_id for line in body.lines], customer
        )
        prepared, total = svc.prepare_lines(repo, body.lines, skus, ctx, order["entity_id"])
        before = snapshot(repo, oid)
        released = order["remaining_exposure"]
        if order["state"] == "APPROVED":
            credit = svc.credit_lock(repo, order["entity_id"], order["customer_id"], validate=False)
            if released != order["gross_amount"] or credit["exposure"] < released:
                raise BusinessError(
                    "CREDIT_INCONSISTENT", "原销售额度占用不一致，请核查后处理", 409
                )
            repo.execute(
                """UPDATE med.credit_account SET exposure=exposure-:g,version=version+1,
                updated_by=:a,updated_at=clock_timestamp() WHERE entity_id=:e AND customer_id=:c""",
                {
                    "g": released,
                    "a": ctx.principal_id,
                    "e": order["entity_id"],
                    "c": order["customer_id"],
                },
            )
        editors = set() if order["state"] == "APPROVED" else set(order["revision_editors"])
        editors.add(ctx.principal_id)
        repo.execute(
            """UPDATE med.sales_order SET customer_id=:c,warehouse_id=:w,delivery_address=:address,
            minimum_shelf_days=:days,credit_days=:credit,gross_amount=:gross,state='DRAFT',
            remaining_exposure=0,approved_by=NULL,approved_at=NULL,customer_snapshot=NULL,
            revision_no=revision_no+1,revision_editors=:editors,version=version+1 WHERE id=:id""",
            {
                "id": oid,
                "c": body.customer_id,
                "w": body.warehouse_id,
                "address": body.delivery_address,
                "days": body.minimum_shelf_days,
                "credit": body.credit_days,
                "gross": total,
                "editors": sorted(editors),
            },
        )
        repo.execute(
            """UPDATE med.inventory_demand SET state='CANCELLED',checks_completed=false,
            version=version+1 WHERE id=:id""",
            {"id": oid},
        )
        repo.execute("DELETE FROM med.inventory_demand_line WHERE demand_id=:id", {"id": oid})
        repo.execute("DELETE FROM med.sales_order_line WHERE order_id=:id", {"id": oid})
        svc.insert_lines(repo, ctx, oid, prepared)
        result = {
            "id": str(oid),
            "version": str(order["version"] + 1),
            "revision_no": order["revision_no"] + 1,
            "state": "DRAFT",
            "released_exposure": format(released, ".2f"),
        }
        audit(
            repo,
            ctx,
            "sales_order",
            oid,
            "sales.revised",
            {**result, "reason": body.reason, "before": before, "after": snapshot(repo, oid)},
        )
        finish_draft(
            repo,
            ctx,
            body.editor_draft,
            "sale",
            order["entity_id"],
            oid,
            oid,
            body.expected_version,
        )
        return result

    return command(
        svc.uow, ctx, "sales.revise", str(oid), key, body.model_dump(mode="json"), execute
    )
