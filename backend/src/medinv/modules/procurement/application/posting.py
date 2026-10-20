import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from medinv.modules.catalog.application.profile import product_snapshot
from medinv.modules.inventory.domain.decimal_values import money_for_posting, portion
from medinv.modules.procurement.api.schemas import ReceiptPost
from medinv.modules.procurement.application.service import ZONE, one, version
from medinv.platform.database.commands import fingerprint
from medinv.platform.database.events import audit, event
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

if TYPE_CHECKING:
    from medinv.modules.procurement.application.service import ProcurementService


def post_receipt(
    service: ProcurementService,
    repo: SqlSession,
    ctx: TenantContext,
    order_id: UUID,
    target: UUID,
    body: ReceiptPost,
) -> dict[str, Any]:
    order, _, _ = service.locked_order(repo, ctx, order_id, "purchase.post", validate=False)
    segment = one(repo, "SELECT * FROM med.receipt_posting WHERE id=:id FOR UPDATE", {"id": target})
    if body.inspection_id != segment["inspection_id"] or body.posting_segment_id != target:
        raise BusinessError("POSTING_SOURCE_MISMATCH", "验收记录或入库子批次不匹配", 422)
    request_hash = fingerprint(body.model_dump(mode="json"))
    if segment["state"] == "POSTED":
        saved = one(
            repo,
            "SELECT result_snapshot FROM med.posting_batch WHERE id=:id",
            {"id": segment["posting_id"]},
        )["result_snapshot"]
        if saved["request_hash"] != request_hash:
            raise BusinessError("POSTING_CONFLICT", "该入库子批次已按另一请求过账", 409)
        return dict(saved["response"])
    version(segment, body.expected_version)
    if order["approval_state"] != "APPROVED":
        raise BusinessError("INVALID_STATE", "采购单不在可入库状态", 409)
    supplier = service.supplier(repo, order["entity_id"], order["supplier_id"])
    inspection = one(
        repo, "SELECT * FROM med.inspection WHERE id=:id", {"id": segment["inspection_id"]}
    )
    receipt = one(
        repo, "SELECT * FROM med.receipt WHERE id=:id FOR UPDATE", {"id": inspection["receipt_id"]}
    )
    lines = repo.rows(
        """SELECT il.id AS inspection_line_id,il.qualified_qty AS post_qty,rl.*,
        ol.unit_price,ol.tax_rate,ol.package_snapshot,ol.qty AS ordered_qty,
        ol.gross_amount AS ordered_gross,ol.net_amount AS ordered_net
        FROM med.inspection_line il JOIN med.receipt_line rl ON (rl.tenant_id,
        rl.id)=(il.tenant_id,il.receipt_line_id)
        JOIN med.purchase_order_line ol ON (ol.tenant_id,ol.id)=(rl.tenant_id,
        rl.order_line_id)
        WHERE il.inspection_id=:id AND il.qualified_qty>0 ORDER BY rl.sku_id,
        rl.lot_id,rl.location_id,il.id FOR UPDATE OF rl""",
        {"id": inspection["id"]},
    )
    if not lines:
        raise BusinessError("NO_QUALIFIED_QUANTITY", "没有可以过账的合格数量", 409)
    skus = service.products(
        repo, ctx, order["entity_id"], [item["sku_id"] for item in lines], supplier
    )
    now = one(repo, "SELECT clock_timestamp() AS now", {})["now"]
    business_date = now.astimezone(ZONE).date()
    period = business_date.strftime("%Y-%m")
    repo.execute(
        """INSERT INTO med.accounting_period(tenant_id,entity_id,period)
        VALUES(:t,:e,:p) ON CONFLICT DO NOTHING""",
        {"t": ctx.tenant_id, "e": order["entity_id"], "p": period},
    )
    accounting = one(
        repo,
        """SELECT state FROM med.accounting_period WHERE entity_id=:e AND period=:p FOR
        SHARE""",
        {"e": order["entity_id"], "p": period},
    )
    if accounting["state"] != "OPEN":
        raise BusinessError("PERIOD_CLOSED", "当前会计期间已关闭", 409)
    lots = repo.rows(
        "SELECT * FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
        {"ids": sorted({item["lot_id"] for item in lines})},
    )
    if any(
        item["quality_blocked"] or not item["expiry_exclusive"] or item["expiry_exclusive"] <= now
        for item in lots
    ):
        raise BusinessError("LOT_INELIGIBLE", "批次已过期或被冻结，不能入库", 422)
    posting_id, event_id = uuid4(), uuid4()
    ledger_ids = [uuid4() for _ in lines]
    result = {
        "posted_document_id": str(target),
        "posting_id": str(posting_id),
        "new_version": str(segment["version"] + 1),
        "ledger_ids": list(map(str, ledger_ids)),
        "event_id": str(event_id),
        "posted_at": now.isoformat(),
    }
    repo.execute(
        """INSERT INTO med.posting_batch(tenant_id,id,created_by,entity_id,source_type,
        source_id,action,
        segment_id,business_date,posted_at,request_id,result_snapshot)
        VALUES(:t,:id,:a,:e,'RECEIPT_POSTING',:source,'RECEIPT_QUALIFIED',:source,
        :day,:now,:request,CAST(:result AS jsonb))""",
        {
            "t": ctx.tenant_id,
            "id": posting_id,
            "a": ctx.principal_id,
            "e": order["entity_id"],
            "source": target,
            "day": business_date,
            "now": now,
            "request": ctx.request_id,
            "result": json.dumps({"request_hash": request_hash, "response": result}),
        },
    )
    gross_total = Decimal(0)
    net_total = Decimal(0)
    for index, (line, ledger_id) in enumerate(zip(lines, ledger_ids, strict=True), 1):
        qty = line["post_qty"]
        sku = skus[line["sku_id"]]
        if qty > line["qualified_qty"] - line["posted_qty"]:
            raise BusinessError(
                "QUALIFIED_QUANTITY_CONSUMED", "合格数量已被其他入库子批次使用", 409
            )
        try:
            if line["package_snapshot"] is not None:
                previous = one(
                    repo,
                    "SELECT coalesce(sum(posted_qty),0) AS qty "
                    "FROM med.receipt_line WHERE order_line_id=:id",
                    {"id": line["order_line_id"]},
                )["qty"]
                gross = portion(
                    line["ordered_gross"], previous, previous + qty, line["ordered_qty"]
                )
                prior_gross = portion(
                    line["ordered_gross"], Decimal(0), previous, line["ordered_qty"]
                )
                net = (
                    portion(
                        line["ordered_net"], prior_gross, prior_gross + gross, line["ordered_gross"]
                    )
                    if line["ordered_gross"]
                    else Decimal(0)
                )
            else:
                gross = money_for_posting(qty * line["unit_price"])
                net = money_for_posting(gross / (Decimal(1) + line["tax_rate"]))
        except ValueError as exc:
            raise BusinessError("AMOUNT_OVERFLOW", "入库金额超出支持范围", 422) from exc
        gross_total += gross
        net_total += net
        params = {
            "t": ctx.tenant_id,
            "a": ctx.principal_id,
            "e": order["entity_id"],
            "w": order["warehouse_id"],
            "loc": line["location_id"],
            "s": line["sku_id"],
            "lot": line["lot_id"],
            "id": uuid4(),
        }
        repo.execute(
            """INSERT INTO med.stock_balance(tenant_id,id,created_by,updated_by,entity_id,
            owner_id,
            warehouse_id,location_id,sku_id,lot_id,quality_status)
            VALUES(:t,:id,:a,:a,:e,:e,:w,:loc,:s,:lot,'QUALIFIED') ON CONFLICT DO
            NOTHING""",
            params,
        )
        balance = one(
            repo,
            """SELECT * FROM med.stock_balance WHERE entity_id=:e AND owner_id=:e AND
            warehouse_id=:w
            AND location_id=:loc AND sku_id=:s AND lot_id=:lot AND
            quality_status='QUALIFIED' FOR UPDATE""",
            params,
        )
        repo.execute(
            """INSERT INTO med.cost_balance(tenant_id,id,created_by,updated_by,entity_id,
            owner_id,sku_id,lot_id,qty,amount)
            VALUES(:t,:id,:a,:a,:e,:e,:s,:lot,0,0) ON CONFLICT DO NOTHING""",
            {**params, "id": uuid4()},
        )
        cost = one(
            repo,
            """SELECT * FROM med.cost_balance WHERE entity_id=:e AND owner_id=:e AND
            sku_id=:s AND lot_id=:lot FOR UPDATE""",
            params,
        )
        if (
            balance["on_hand"] + qty >= Decimal("1e14")
            or cost["qty"] + qty >= Decimal("1e14")
            or cost["amount"] + net >= Decimal("1e16")
        ):
            raise BusinessError("BALANCE_OVERFLOW", "库存或成本池超过支持范围", 422)
        posting_line = uuid4()
        repo.execute(
            """INSERT INTO med.posting_line(tenant_id,id,created_by,posting_id,
            source_line_id,line_no,sku_id,
            base_qty,product_snapshot,package_snapshot,gross_amount,net_amount)
            VALUES(:t,:id,:a,:p,:source,:n,:s,:q,CAST(:product AS jsonb),CAST(:package
            AS jsonb),:gross,:net)""",
            {
                "t": ctx.tenant_id,
                "id": posting_line,
                "gross": gross,
                "net": net,
                "a": ctx.principal_id,
                "p": posting_id,
                "source": line["inspection_line_id"],
                "n": index,
                "s": line["sku_id"],
                "q": qty,
                "product": json.dumps(product_snapshot(sku)),
                "package": json.dumps(
                    line["package_snapshot"]
                    or {
                        "base_unit": sku["base_unit"],
                        "qty_scale": sku["qty_scale"],
                        "numerator": 1,
                        "denominator": 1,
                    }
                ),
            },
        )
        repo.execute(
            """UPDATE med.stock_balance SET on_hand=on_hand+:q,version=version+1,
            updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"q": qty, "a": ctx.principal_id, "id": balance["id"]},
        )
        repo.execute(
            """UPDATE med.cost_balance SET qty=qty+:q,amount=amount+:v,version=version+1,
            updated_by=:a,updated_at=clock_timestamp() WHERE id=:id""",
            {"q": qty, "v": net, "a": ctx.principal_id, "id": cost["id"]},
        )
        repo.execute(
            """INSERT INTO med.stock_ledger(tenant_id,id,created_by,posting_line_id,
            effect_index,balance_id,
            qty_delta,event_type,posted_at) VALUES(:t,:id,:a,:p,0,:b,:q,
            'RECEIPT_QUALIFIED',:now)""",
            {
                "t": ctx.tenant_id,
                "id": ledger_id,
                "a": ctx.principal_id,
                "p": posting_line,
                "b": balance["id"],
                "q": qty,
                "now": now,
            },
        )
        repo.execute(
            """INSERT INTO med.cost_ledger(tenant_id,id,created_by,posting_line_id,
            effect_index,cost_balance_id,
            qty_delta,amount_delta,business_date) VALUES(:t,:id,:a,:p,0,:b,:q,:v,:day)""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "a": ctx.principal_id,
                "p": posting_line,
                "b": cost["id"],
                "q": qty,
                "v": net,
                "day": business_date,
            },
        )
        repo.execute(
            "UPDATE med.receipt_line SET posted_qty=posted_qty+:q WHERE id=:id",
            {"q": qty, "id": line["id"]},
        )
    try:
        money_for_posting(gross_total)
        money_for_posting(net_total)
    except ValueError as exc:
        raise BusinessError("AMOUNT_OVERFLOW", "应付总额超出支持范围", 422) from exc
    repo.execute(
        """INSERT INTO med.payable_obligation(tenant_id,id,entity_id,supplier_id,
        posting_id,
        gross_amount,net_amount,tax_amount,policy_version) VALUES(:t,:id,:e,:s,:p,
        :gross,:net,:tax,'RECEIPT_NET_V1')""",
        {
            "t": ctx.tenant_id,
            "id": uuid4(),
            "e": order["entity_id"],
            "s": order["supplier_id"],
            "p": posting_id,
            "gross": gross_total,
            "net": net_total,
            "tax": gross_total - net_total,
        },
    )
    repo.execute(
        """UPDATE med.receipt_posting SET state='POSTED',version=version+1,
        posting_id=:p WHERE id=:id""",
        {"p": posting_id, "id": target},
    )
    repo.execute("UPDATE med.receipt SET version=version+1 WHERE id=:id", {"id": receipt["id"]})
    audit(repo, ctx, "receipt_posting", target, "inventory.receipt_posted", result)
    event(
        repo,
        ctx,
        event_id,
        "receipt_posting",
        target,
        segment["version"] + 1,
        "inventory.receipt_posted",
        result,
    )
    return result
