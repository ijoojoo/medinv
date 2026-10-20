import json
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.schemas import ReleaseRequest, ReservationLine, ReserveRequest
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.procurement.application.service import document_number, one, version
from medinv.modules.sales.api.schemas import Pick, ShipmentCreate, Verify
from medinv.modules.sales.application.service import SalesService
from medinv.platform.database.commands import command, fingerprint
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def shipment_order(service: SalesService, ctx: TenantContext, sid: UUID, permission: str) -> UUID:
    ctx.require(permission)
    with service.uow.open(ctx, readonly=True) as session:
        row = one(
            SqlSession(session), "SELECT order_id FROM med.shipment WHERE id=:id", {"id": sid}
        )
    oid = UUID(str(row["order_id"]))
    service.scope(ctx, permission, oid)
    return oid


def allocate(
    service: SalesService, ctx: TenantContext, oid: UUID, body: ShipmentCreate, key: str
) -> dict[str, Any]:
    service.scope(ctx, "sales.allocate", oid)
    request_hash = fingerprint(body.model_dump(mode="json"))

    def execute(repo: SqlSession) -> dict[str, Any]:
        order, lines, _ = service.locked_order(repo, ctx, oid, "sales.allocate", False)
        prior = repo.rows(
            "SELECT * FROM med.shipment WHERE client_operation_id=:op",
            {"op": body.client_operation_id},
        )
        if prior:
            p = prior[0]
            if (
                p["order_id"] != oid
                or p["created_by"] != ctx.principal_id
                or p["request_hash"] != request_hash
            ):
                raise BusinessError("OPERATION_CONFLICT", "分配操作编号已被另一请求使用", 409)
            return dict(p["creation_result"])
        version(order, body.expected_version)
        if order["state"] != "APPROVED":
            raise BusinessError("INVALID_STATE", "仅已审核且未关闭的订单可分配", 409)
        customer = service.customer(repo, order["entity_id"], order["customer_id"])
        service.products(repo, order["entity_id"], [entry["sku_id"] for entry in lines], customer)
        service.credit_lock(repo, order["entity_id"], order["customer_id"])
        demand = one(
            repo, "SELECT version FROM med.inventory_demand WHERE id=:id FOR UPDATE", {"id": oid}
        )
        by_id = {line["id"]: line for line in lines}
        if any(line.order_line_id not in by_id for line in body.lines):
            raise BusinessError("SOURCE_LINE_MISMATCH", "分配行不属于当前销售单", 422)
        req = ReserveRequest(
            client_operation_id=body.client_operation_id,
            entity_id=order["entity_id"],
            warehouse_id=order["warehouse_id"],
            source_type="SALES_ORDER",
            source_id=oid,
            expected_source_version=str(demand["version"]),
            lines=[
                ReservationLine(
                    source_line_id=entry.order_line_id,
                    sku_id=by_id[entry.order_line_id]["sku_id"],
                    base_qty=entry.qty,
                )
                for entry in body.lines
            ],
        )
        # Reuse allocation within this same business transaction: no nested UOW/commit.
        result = service.inventory._reserve(
            repo,
            ctx,
            req,
            fingerprint(req.model_dump(mode="json")),
            minimum_expiry=service.now(repo) + timedelta(days=order["minimum_shelf_days"]),
        )
        sid = uuid4()
        response = {"id": str(sid), "number": document_number("SH", sid), "version": "1"}
        repo.execute(
            """INSERT INTO med.shipment(tenant_id,id,order_id,reservation_id,number,
            created_by,client_operation_id,request_hash,creation_result)
            VALUES(:t,:id,:o,:r,:n,:a,:op,:hash,CAST(:result AS jsonb))""",
            {
                "t": ctx.tenant_id,
                "id": sid,
                "o": oid,
                "r": UUID(result["reservation_id"]),
                "n": response["number"],
                "a": ctx.principal_id,
                "op": body.client_operation_id,
                "hash": request_hash,
                "result": json.dumps(response),
            },
        )
        for item in result["allocations"]:
            repo.execute(
                """INSERT INTO med.shipment_line(tenant_id,id,shipment_id,allocation_id)
                VALUES(:t,:id,:s,:a)""",
                {"t": ctx.tenant_id, "id": uuid4(), "s": sid, "a": UUID(item["id"])},
            )
        repo.execute("UPDATE med.sales_order SET version=version+1 WHERE id=:id", {"id": oid})
        audit(repo, ctx, "shipment", sid, "sales.allocated", body.model_dump(mode="json"))
        return response

    return command(
        service.uow,
        ctx,
        "sales.allocate",
        str(oid),
        key,
        body.model_dump(mode="json"),
        execute,
        body.client_operation_id,
    )


def active_manifest(
    service: SalesService, repo: SqlSession, order: dict[str, Any], shipment: dict[str, Any]
) -> list[dict[str, Any]]:
    reservation = one(
        repo,
        "SELECT * FROM med.reservation WHERE id=:id FOR UPDATE",
        {"id": shipment["reservation_id"]},
    )
    now = service.now(repo)
    if reservation["state"] != "ACTIVE" or reservation["expires_at"] <= now:
        raise BusinessError("RESERVATION_EXPIRED", "预占已失效，请取消子单释放后重新分配", 409)
    if order["state"] != "APPROVED":
        raise BusinessError("INVALID_STATE", "销售订单不允许继续履约", 409)
    service.credit_lock(repo, order["entity_id"], order["customer_id"])
    rows = repo.rows(
        """SELECT sl.*,a.allocated_qty,a.released_qty,a.consumed_qty,a.source_line_id,
        a.balance_id,b.sku_id,b.lot_id,b.location_id,b.owner_id,b.on_hand,b.reserved,b.blocked,
        b.quality_status,l.lot_no,l.expiry_exclusive,l.quality_blocked
        FROM med.shipment_line sl JOIN med.reservation_allocation a
        ON (a.tenant_id,a.id)=(sl.tenant_id,sl.allocation_id)
        JOIN med.stock_balance b ON (b.tenant_id,b.id)=(a.tenant_id,a.balance_id)
        JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
        WHERE sl.shipment_id=:id ORDER BY sl.id FOR UPDATE OF sl,a,b""",
        {"id": shipment["id"]},
    )
    repo.rows(
        "SELECT id FROM med.lot WHERE id=ANY(:ids) ORDER BY id FOR SHARE",
        {"ids": sorted({r["lot_id"] for r in rows})},
    )
    # Re-read after lot locks; a concurrent quality update must not be missed.
    lots = {
        entry["id"]: entry
        for entry in repo.rows(
            "SELECT * FROM med.lot WHERE id=ANY(:ids)", {"ids": sorted({r["lot_id"] for r in rows})}
        )
    }
    for row in rows:
        lot = lots[row["lot_id"]]
        if (
            row["quality_status"] != "QUALIFIED"
            or lot["quality_blocked"]
            or not lot["expiry_exclusive"]
            or lot["expiry_exclusive"] < now + timedelta(days=order["minimum_shelf_days"])
            or lot["expiry_exclusive"] <= now
            or row["released_qty"]
            or row["consumed_qty"]
            or row["reserved"] < row["allocated_qty"]
            or row["on_hand"] - row["blocked"] < row["allocated_qty"]
        ):
            raise BusinessError(
                "SHIPMENT_INELIGIBLE", "批次、效期或预占数量变化，请核查后重新分配", 409
            )
    if not rows:
        raise BusinessError("EMPTY_SHIPMENT", "发货子单没有分配明细", 409)
    return rows


def pick_or_verify(
    service: SalesService,
    ctx: TenantContext,
    sid: UUID,
    body: Pick | Verify,
    key: str,
    verify: bool,
) -> dict[str, Any]:
    permission = "sales.verify" if verify else "sales.pick"
    oid = shipment_order(service, ctx, sid, permission)

    def execute(repo: SqlSession) -> dict[str, Any]:
        order, _, _ = service.locked_order(repo, ctx, oid, permission)
        sh = one(repo, "SELECT * FROM med.shipment WHERE id=:id FOR UPDATE", {"id": sid})
        version(sh, body.expected_version)
        if sh["state"] != ("PICKED" if verify else "ALLOCATED"):
            raise BusinessError("INVALID_STATE", "子单状态已变化，请刷新", 409)
        if verify and sh["picked_by"] == ctx.principal_id:
            raise BusinessError("SEPARATION_REQUIRED", "拣货人与复核人必须不同", 403)
        rows = active_manifest(service, repo, order, sh)
        provided = {entry.shipment_line_id: entry for entry in body.lines}
        if set(provided) != {r["id"] for r in rows}:
            raise BusinessError("MANIFEST_MISMATCH", "必须逐行确认本子单全部分配明细", 422)
        for row in rows:
            p = provided[row["id"]]
            if Decimal(p.qty) != row["allocated_qty"] or p.lot_no != row["lot_no"]:
                raise BusinessError("PICK_MISMATCH", "实物批号或数量与分配明细不一致", 422)
            if not verify:
                repo.execute(
                    "UPDATE med.shipment_line SET picked_qty=:q,scanned_lot_no=:lot WHERE id=:id",
                    {"q": Decimal(p.qty), "lot": p.lot_no, "id": row["id"]},
                )
        if verify:
            assert isinstance(body, Verify)
            repo.execute(
                """UPDATE med.shipment SET state='VERIFIED',version=version+1,
                verified_by=:a,verified_at=clock_timestamp(),verify_note=:note,shipping_ref=:ref
                WHERE id=:id""",
                {"a": ctx.principal_id, "note": body.note, "ref": body.shipping_ref, "id": sid},
            )
        else:
            repo.execute(
                """UPDATE med.shipment SET state='PICKED',version=version+1,
                picked_by=:a,picked_at=clock_timestamp(),pick_note=:note WHERE id=:id""",
                {"a": ctx.principal_id, "note": body.note, "id": sid},
            )
        state = "VERIFIED" if verify else "PICKED"
        audit(repo, ctx, "shipment", sid, "sales." + state.lower(), body.model_dump(mode="json"))
        return {"id": str(sid), "version": str(sh["version"] + 1), "state": state}

    return command(
        service.uow,
        ctx,
        "sales.verify" if verify else "sales.pick",
        str(sid),
        key,
        body.model_dump(mode="json"),
        execute,
    )


def cancel(
    service: SalesService, ctx: TenantContext, sid: UUID, body: Transition, key: str
) -> dict[str, Any]:
    oid = shipment_order(service, ctx, sid, "sales.write")

    def execute(repo: SqlSession) -> dict[str, Any]:
        service.locked_order(repo, ctx, oid, "sales.write", False)
        sh = one(repo, "SELECT * FROM med.shipment WHERE id=:id FOR UPDATE", {"id": sid})
        if sh["state"] == "CANCELLED":
            return {"id": str(sid), "version": str(sh["version"]), "state": "CANCELLED"}
        version(sh, body.expected_version)
        if sh["state"] == "POSTED":
            raise BusinessError("INVALID_STATE", "已出库子单不能取消，需走退货流程", 409)
        r = one(
            repo, "SELECT version FROM med.reservation WHERE id=:id", {"id": sh["reservation_id"]}
        )
        service.inventory._release(
            repo,
            ctx,
            sh["reservation_id"],
            ReleaseRequest(expected_version=str(r["version"]), reason=body.reason),
            permission="sales.write",
        )
        repo.execute(
            "UPDATE med.shipment SET state='CANCELLED',version=version+1 WHERE id=:id", {"id": sid}
        )
        repo.execute("UPDATE med.sales_order SET version=version+1 WHERE id=:id", {"id": oid})
        audit(repo, ctx, "shipment", sid, "sales.shipment_cancelled", body.model_dump(mode="json"))
        return {"id": str(sid), "version": str(sh["version"] + 1), "state": "CANCELLED"}

    return command(
        service.uow,
        ctx,
        "sales.shipment.cancel",
        str(sid),
        key,
        body.model_dump(mode="json"),
        execute,
    )
