import json
from datetime import datetime
from decimal import localcontext
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.schemas import ReleaseRequest, ReserveRequest
from medinv.modules.inventory.domain.allocation import Candidate, allocate_fefo
from medinv.modules.inventory.domain.decimal_values import quantity
from medinv.modules.inventory.infrastructure.repository import InventoryRepository
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

# Kept in one query so reporting and reservation use the same eligibility rules.
BALANCES = """
 SELECT b.*, l.expiry_exclusive, s.qty_scale,
   loc.code AS location_code,coalesce(loc.name,loc.code) AS location_name,
   array_remove(ARRAY[
     CASE WHEN w.kind='TRANSIT' THEN 'IN_TRANSIT' END,
     CASE WHEN e.status<>'ACTIVE' THEN 'ENTITY_SUSPENDED' END,
     CASE WHEN g.id IS NULL OR g.blocked THEN 'SKU_BLOCKED' END,
     CASE WHEN p.approval_state<>'APPROVED' THEN 'PRODUCT_UNAPPROVED' END,
     CASE WHEN p.restricted_category IS NOT NULL THEN 'RESTRICTED_PRODUCT' END,
     CASE WHEN s.serial_required THEN 'SERIAL_ALLOCATION_NOT_ENABLED' END,
     CASE WHEN b.quality_status<>'QUALIFIED' THEN 'QUALITY_NOT_QUALIFIED' END,
     CASE WHEN EXISTS (SELECT 1 FROM med.stock_count_line scl
       JOIN med.stock_count sc ON (sc.tenant_id,sc.id)=(scl.tenant_id,scl.count_id)
       JOIN med.cost_balance cb ON (cb.tenant_id,cb.id)=(scl.tenant_id,scl.cost_balance_id)
       WHERE sc.state IN ('COUNTING','SUBMITTED','APPROVED')
         AND (cb.tenant_id,cb.entity_id,cb.sku_id,cb.lot_id)=
             (b.tenant_id,b.entity_id,b.sku_id,b.lot_id)) THEN 'COUNT_FROZEN' END,

     CASE WHEN s.lot_required AND l.id IS NULL THEN 'LOT_REQUIRED' END,
     CASE WHEN l.quality_blocked THEN 'LOT_BLOCKED' END,
     CASE WHEN l.expiry_kind='UNRESOLVED' THEN 'EXPIRY_UNRESOLVED' END,
     CASE WHEN l.expiry_exclusive<=clock_timestamp() THEN 'EXPIRED' END
   ], NULL) AS ineligibility_codes
 FROM med.stock_balance b
 JOIN med.location loc ON (loc.tenant_id,loc.id)=(b.tenant_id,b.location_id)
 JOIN med.warehouse w ON (w.tenant_id,w.id)=(b.tenant_id,b.warehouse_id)
 JOIN med.legal_entity e ON (e.tenant_id,e.id)=(b.tenant_id,b.entity_id)
 JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
 JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
 LEFT JOIN med.stock_gate g ON (g.tenant_id,g.entity_id,g.sku_id)=
                              (b.tenant_id,b.entity_id,b.sku_id)
 LEFT JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
 WHERE b.entity_id=:entity AND b.warehouse_id=:warehouse AND b.sku_id=ANY(:skus)
"""


def emit(
    repo: InventoryRepository,
    ctx: TenantContext,
    resource_id: UUID,
    version: int,
    action: str,
    payload: dict[str, Any],
    reason: str | None = None,
) -> None:
    params = {
        "tenant": ctx.tenant_id,
        "actor": ctx.principal_id,
        "resource": resource_id,
        "request": ctx.request_id,
        "version": version,
        "action": action,
        "payload": json.dumps(payload),
        "reason": reason,
        "event": uuid4(),
    }
    repo.execute(
        """
        INSERT INTO med.audit_event
          (tenant_id,id,created_by,actor_subject,action,resource_type,resource_id,
                request_id,reason)
        VALUES (:tenant,:id,:actor,CAST(:actor AS text),:action,'reservation',:resource,
                :request,:reason)
    """,
        {**params, "id": uuid4()},
    )
    repo.execute(
        """
        INSERT INTO med.outbox_event
          (tenant_id,id,created_by,aggregate_type,aggregate_id,aggregate_version,
           event_type,schema_version,occurred_at,correlation_id,payload)
        VALUES (:tenant,:event,:actor,'reservation',:resource,:version,
                :action,1,clock_timestamp(),:request,CAST(:payload AS jsonb))
    """,
        params,
    )
    repo.execute(
        """
        INSERT INTO med.outbox_delivery
          (tenant_id,id,created_by,updated_by,event_id,consumer,state,next_attempt_at)
        VALUES (:tenant,:id,:actor,:actor,:event,'inventory_projection','READY',now())
    """,
        {**params, "id": uuid4()},
    )


class InventoryService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def options(self, ctx: TenantContext, entity: UUID, offset: int = 0) -> dict[str, Any]:
        ctx.require("inventory.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = InventoryRepository(session)
            warehouses = repo.rows(
                """SELECT id,name FROM med.warehouse
                WHERE entity_id=:entity AND archived_at IS NULL
                ORDER BY name,id LIMIT 201 OFFSET :offset""",
                {"entity": entity, "offset": offset},
            )
            skus = repo.rows(
                """SELECT DISTINCT s.id,p.name || ' · ' || s.code AS name
                FROM med.stock_balance b JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                WHERE b.entity_id=:entity ORDER BY name,s.id LIMIT 201 OFFSET :offset""",
                {"entity": entity, "offset": offset},
            )
            has_more = len(warehouses) > 200 or len(skus) > 200
            return {
                "warehouses": [{"id": str(r["id"]), "name": r["name"]} for r in warehouses[:200]],
                "skus": [{"id": str(r["id"]), "name": r["name"]} for r in skus[:200]],
                "has_more": has_more,
                "next_offset": offset + 200 if has_more else None,
            }

    def availability(
        self, ctx: TenantContext, entity: UUID, warehouse: UUID, sku: UUID
    ) -> dict[str, Any]:
        ctx.require("inventory.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            repo = InventoryRepository(session)
            records = repo.rows(
                BALANCES + " ORDER BY b.id LIMIT 201",
                {"entity": entity, "warehouse": warehouse, "skus": [sku]},
            )
            if len(records) > 200:
                raise BusinessError("RESULT_TOO_LARGE", "库位过多，需使用后续分页查询接口", 422)
            now = repo.rows("SELECT clock_timestamp() AS now", {})[0]["now"]
            items = []
            for row in records:
                codes = row["ineligibility_codes"]
                with localcontext() as decimal_ctx:
                    decimal_ctx.prec = 50
                    available = (
                        row["on_hand"] - row["reserved"] - row["blocked"] if not codes else 0
                    )
                items.append(
                    {
                        "balance_id": str(row["id"]),
                        "sku_id": str(row["sku_id"]),
                        "lot_id": str(row["lot_id"]) if row["lot_id"] else None,
                        "warehouse_id": str(row["warehouse_id"]),
                        "location_id": str(row["location_id"]),
                        "location_code": row["location_code"],
                        "location_name": row["location_name"],
                        **{
                            key: format(row[key], ".6f")
                            for key in ("on_hand", "reserved", "blocked")
                        },
                        "available": format(available, ".6f"),
                        "eligible": not codes,
                        "ineligibility_codes": codes,
                        "version": str(row["version"]),
                    }
                )
            return {"items": items, "data_as_of": now.isoformat()}

    def reserve(self, ctx: TenantContext, request: ReserveRequest, key: str) -> dict[str, Any]:
        ctx.require("inventory.reserve", request.entity_id)
        body = request.model_dump(mode="json")
        return command(
            self.uow,
            ctx,
            "inventory.reserve",
            str(request.source_id),
            key,
            body,
            lambda repo: self._reserve(repo, ctx, request, fingerprint(body)),
            request.client_operation_id,
        )

    def _reserve(
        self,
        repo: InventoryRepository,
        ctx: TenantContext,
        req: ReserveRequest,
        request_hash: str,
        *,
        minimum_expiry: datetime | None = None,
        retail: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "tenant": ctx.tenant_id,
            "actor": ctx.principal_id,
            "entity": req.entity_id,
            "warehouse": req.warehouse_id,
            "source": req.source_id,
            "operation": req.client_operation_id,
            "skus": sorted({line.sku_id for line in req.lines}),
        }
        prior = repo.rows(
            "SELECT * FROM med.command_result WHERE client_operation_id=:operation", params
        )
        if prior:
            row = prior[0]
            if row["principal_id"] != ctx.principal_id or row["request_hash"] != request_hash:
                raise BusinessError("OPERATION_CONFLICT", "业务操作编号已被其他请求使用")
            return dict(row["result_snapshot"])
        # Retail admission is internal; the public reserve endpoint cannot bypass POS rules.
        if req.source_type != "SALES_ORDER" and not (retail and req.source_type == "RETAIL_SALE"):
            raise BusinessError("SOURCE_NOT_ENABLED", "请通过对应业务单据完成前置校验", 422)
        entities = repo.rows(
            "SELECT status FROM med.legal_entity WHERE id=:entity FOR SHARE", params
        )
        if not entities or entities[0]["status"] != "ACTIVE":
            raise BusinessError("ENTITY_UNAVAILABLE", "主体不存在或已停用", 404)
        if req.source_type == "SALES_ORDER":
            from medinv.modules.catalog.application.admission import reserve_admission

            reserve_admission(repo, req.entity_id, req.source_id, params["skus"])
        gates = repo.rows(
            """SELECT * FROM med.stock_gate
            WHERE entity_id=:entity AND sku_id=ANY(:skus) ORDER BY sku_id FOR SHARE""",
            params,
        )
        if len(gates) != len(params["skus"]) or any(row["blocked"] for row in gates):
            raise BusinessError("SKU_BLOCKED", "商品不可分配")
        skus = repo.rows("SELECT * FROM med.sku WHERE id=ANY(:skus) ORDER BY id FOR SHARE", params)
        if len(skus) != len(params["skus"]):
            raise BusinessError("SKU_NOT_FOUND", "商品不可见或不存在", 404)
        params["products"] = sorted({row["product_id"] for row in skus})
        repo.rows(
            "SELECT id FROM med.product WHERE id=ANY(:products) ORDER BY id FOR SHARE", params
        )
        # Candidate IDs are read before locks. New receipts are considered on the next request.
        from medinv.modules.catalog.application.entity_settings import entity_product_allowed

        entity_product_allowed(repo, req.entity_id, params["skus"], "sales")
        candidates = repo.rows(BALANCES + " ORDER BY b.id LIMIT 2001", params)
        if len(candidates) > 2000:
            raise BusinessError("ALLOCATION_TOO_LARGE", "候选库存超过在线分配上限", 422)
        lot_ids = sorted({row["lot_id"] for row in candidates if row["lot_id"] is not None})
        if lot_ids:
            repo.rows(
                "SELECT id FROM med.lot WHERE id=ANY(:lots) ORDER BY id FOR SHARE",
                {"lots": lot_ids},
            )
        sources = repo.rows(
            "SELECT * FROM med.inventory_demand WHERE id=:source FOR UPDATE", params
        )
        if not sources:
            raise BusinessError("SOURCE_NOT_FOUND", "来源单据不可见或不存在", 404)
        source = sources[0]
        now: datetime = repo.rows("SELECT clock_timestamp() AS now", {})[0]["now"]
        if (
            source["entity_id"] != req.entity_id
            or source["warehouse_id"] != req.warehouse_id
            or source["source_type"] != req.source_type
        ):
            raise BusinessError("SOURCE_MISMATCH", "来源单据与主体、仓库不一致")
        if source["version"] != int(req.expected_source_version):
            raise BusinessError("VERSION_CONFLICT", "来源单据已变更")
        if (
            source["state"] != "APPROVED"
            or not source["checks_completed"]
            or source["eligible_until"] <= now
        ):
            raise BusinessError("SOURCE_NOT_APPROVED", "来源单据前置审核未通过或已失效")
        lines = repo.rows(
            """SELECT * FROM med.inventory_demand_line
            WHERE demand_id=:source ORDER BY id FOR UPDATE""",
            params,
        )
        by_line = {line["id"]: line for line in lines}
        by_sku = {sku["id"]: sku for sku in skus}
        needed = {}
        for line in req.lines:
            source_line = by_line.get(line.source_line_id)
            if source_line is None or source_line["sku_id"] != line.sku_id:
                raise BusinessError("SOURCE_LINE_MISMATCH", "单据明细与商品不一致")
            try:
                qty = quantity(line.base_qty, sku_scale=by_sku[line.sku_id]["qty_scale"])
            except ValueError as exc:
                raise BusinessError("INVALID_QUANTITY", "数量不符合商品精度", 422) from exc
            if (
                qty
                > source_line["requested_qty"]
                - source_line["reserved_qty"]
                - source_line["fulfilled_qty"]
            ):
                raise BusinessError("DEMAND_EXCEEDED", "预占数量超过单据剩余需求")
            needed[line.source_line_id] = qty
        params["balances"] = [row["id"] for row in candidates]
        if params["balances"]:
            # Uniform UUID order for row locks; FEFO is evaluated after every balance is locked.
            repo.rows(
                "SELECT id FROM med.stock_balance WHERE id=ANY(:balances) ORDER BY id FOR UPDATE",
                params,
            )
            candidates = repo.rows(BALANCES + " AND b.id=ANY(:balances) ORDER BY b.id", params)
        available = {
            row["id"]: row["on_hand"] - row["reserved"] - row["blocked"] for row in candidates
        }
        allocations: list[dict[str, Any]] = []
        for line in req.lines:
            try:
                chosen = allocate_fefo(
                    [
                        Candidate(row["id"], row["expiry_exclusive"], available[row["id"]])
                        for row in candidates
                        if row["sku_id"] == line.sku_id
                        and not row["ineligibility_codes"]
                        and (
                            minimum_expiry is None
                            or (
                                row["expiry_exclusive"] is not None
                                and row["expiry_exclusive"] >= minimum_expiry
                            )
                        )
                    ],
                    needed[line.source_line_id],
                )
            except ValueError as exc:
                if any(
                    r["sku_id"] == line.sku_id and "COUNT_FROZEN" in r["ineligibility_codes"]
                    for r in candidates
                ):
                    raise BusinessError(
                        "COUNT_FROZEN", "部分批次正在盘点，可分配库存不足，请完成或取消盘点", 409
                    ) from exc
                raise BusinessError("INSUFFICIENT_STOCK", "合格且未过期的可用库存不足") from exc
            for balance_id, qty in chosen:
                available[balance_id] -= qty
                allocations.append(
                    {
                        "id": str(uuid4()),
                        "source_line_id": str(line.source_line_id),
                        "balance_id": str(balance_id),
                        "base_qty": format(qty, ".6f"),
                    }
                )
        if len(allocations) > 500:
            raise BusinessError("ALLOCATION_TOO_LARGE", "拆分库位超过在线分配上限", 422)
        reservation_id = uuid4()
        result_row = repo.rows(
            """
            INSERT INTO med.reservation
              (tenant_id,id,created_by,updated_by,entity_id,warehouse_id,source_type,
                source_id,
               client_operation_id,state,expires_at)
            VALUES (:tenant,:id,:actor,:actor,:entity,:warehouse,:source_type,:source,
                :operation,
                    'ACTIVE',clock_timestamp()+make_interval(secs=>:ttl)) RETURNING
                expires_at
        """,
            {
                **params,
                "id": reservation_id,
                "source_type": req.source_type,
                "ttl": self.uow.settings.reservation_ttl_seconds,
            },
        )[0]
        for allocation in allocations:
            values = {
                **params,
                "reservation": reservation_id,
                "id": UUID(allocation["id"]),
                "line": UUID(allocation["source_line_id"]),
                "balance": UUID(allocation["balance_id"]),
                "qty": allocation["base_qty"],
            }
            repo.execute(
                """INSERT INTO med.reservation_allocation
                (tenant_id,id,created_by,updated_by,reservation_id,source_line_id,
                balance_id,allocated_qty)
                VALUES (:tenant,:id,:actor,:actor,:reservation,:line,:balance,CAST(:qty
                AS numeric))""",
                values,
            )
            repo.execute(
                """UPDATE med.stock_balance SET reserved=reserved+CAST(:qty AS numeric),
                version=version+1,updated_at=clock_timestamp(),updated_by=:actor WHERE
                id=:balance""",
                values,
            )
            repo.execute(
                """UPDATE med.inventory_demand_line
                SET reserved_qty=reserved_qty+CAST(:qty AS numeric)
                WHERE id=:line""",
                values,
            )
        repo.execute("UPDATE med.inventory_demand SET version=version+1 WHERE id=:source", params)
        result = {
            "reservation_id": str(reservation_id),
            "version": "1",
            "state": "ACTIVE",
            "expires_at": result_row["expires_at"].isoformat(),
            "allocations": allocations,
        }
        repo.execute(
            """INSERT INTO med.command_result
            (tenant_id,client_operation_id,principal_id,entity_id,request_hash,
                result_snapshot)
            VALUES (:tenant,:operation,:actor,:entity,:hash,CAST(:result AS jsonb))""",
            {**params, "hash": request_hash, "result": json.dumps(result)},
        )
        emit(repo, ctx, reservation_id, 1, "inventory.reserved", result)
        return result

    def release(
        self, ctx: TenantContext, reservation_id: UUID, request: ReleaseRequest, key: str
    ) -> dict[str, Any]:
        ctx.require("inventory.release")
        # Entity scope must be checked before cached idempotent responses as well.
        with self.uow.open(ctx, readonly=True) as session:
            rows = InventoryRepository(session).rows(
                "SELECT entity_id FROM med.reservation WHERE id=:id", {"id": reservation_id}
            )
            if not rows:
                raise BusinessError("NOT_FOUND", "预占记录不可见或不存在", 404)
            ctx.require("inventory.release", rows[0]["entity_id"])
        return command(
            self.uow,
            ctx,
            "inventory.release",
            str(reservation_id),
            key,
            request.model_dump(mode="json"),
            lambda repo: self._release(repo, ctx, reservation_id, request),
        )

    def _release(
        self,
        repo: InventoryRepository,
        ctx: TenantContext,
        reservation_id: UUID,
        req: ReleaseRequest,
        *,
        retail: bool = False,
        permission: str = "inventory.release",
    ) -> dict[str, Any]:
        params = {"id": reservation_id, "actor": ctx.principal_id}
        hint = repo.rows("SELECT source_id,entity_id FROM med.reservation WHERE id=:id", params)
        if not hint:
            raise BusinessError("NOT_FOUND", "预占记录不可见或不存在", 404)
        repo.rows(
            "SELECT id FROM med.legal_entity WHERE id=:e FOR SHARE", {"e": hint[0]["entity_id"]}
        )
        source = repo.rows(
            "SELECT * FROM med.inventory_demand WHERE id=:source FOR UPDATE",
            {"source": hint[0]["source_id"]},
        )[0]
        if source["source_type"] == "RETAIL_SALE" and not retail:
            raise BusinessError("RETAIL_WORKFLOW_REQUIRED", "请在零售单中取消或办理退款", 409)
        repo.rows(
            "SELECT id FROM med.inventory_demand_line WHERE demand_id=:source "
            "ORDER BY id FOR UPDATE",
            {"source": source["id"]},
        )
        allocation_rows = repo.rows(
            "SELECT * FROM med.reservation_allocation WHERE reservation_id=:id", params
        )
        balance_ids = sorted({row["balance_id"] for row in allocation_rows})
        if balance_ids:
            repo.rows(
                "SELECT id FROM med.stock_balance WHERE id=ANY(:ids) ORDER BY id FOR UPDATE",
                {"ids": balance_ids},
            )
        row = repo.rows("SELECT * FROM med.reservation WHERE id=:id FOR UPDATE", params)[0]
        ctx.require(permission, row["entity_id"])
        if row["state"] == "RELEASED":
            return {
                "id": str(reservation_id),
                "state": "RELEASED",
                "new_version": str(row["version"]),
            }
        if row["version"] != int(req.expected_version):
            raise BusinessError("VERSION_CONFLICT", "预占记录已变更")
        if row["state"] != "ACTIVE":
            raise BusinessError("INVALID_STATE", "当前预占状态不允许释放")
        if row["protected_until"] is not None or source["state"] == "PAID_PROTECTED":
            raise BusinessError("PAYMENT_PROTECTED", "已付款保护单须通过恢复或退款流程处理")
        for allocation in allocation_rows:
            remaining = (
                allocation["allocated_qty"]
                - allocation["released_qty"]
                - allocation["consumed_qty"]
            )
            values = {
                **params,
                "qty": remaining,
                "balance": allocation["balance_id"],
                "line": allocation["source_line_id"],
                "allocation": allocation["id"],
            }
            repo.execute(
                """UPDATE med.stock_balance SET reserved=reserved-:qty,
                version=version+1,updated_at=clock_timestamp(),updated_by=:actor WHERE
                id=:balance""",
                values,
            )
            repo.execute(
                "UPDATE med.inventory_demand_line SET reserved_qty=reserved_qty-:qty "
                "WHERE id=:line",
                values,
            )
            repo.execute(
                """UPDATE med.reservation_allocation SET released_qty=released_qty+:qty,
                version=version+1,updated_at=clock_timestamp(),updated_by=:actor WHERE
                id=:allocation""",
                values,
            )
        repo.execute(
            """UPDATE med.reservation SET state='RELEASED',version=version+1,
            updated_at=clock_timestamp(),updated_by=:actor WHERE id=:id""",
            params,
        )
        repo.execute(
            "UPDATE med.inventory_demand SET version=version+1 WHERE id=:source",
            {"source": source["id"]},
        )
        result = {
            "id": str(reservation_id),
            "state": "RELEASED",
            "new_version": str(row["version"] + 1),
        }
        emit(
            repo, ctx, reservation_id, row["version"] + 1, "inventory.released", result, req.reason
        )
        return result
