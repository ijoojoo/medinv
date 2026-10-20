"""Physical custody before acceptance: no owned stock, cost, AR or credit effects."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.procurement.application.service import one, version
from medinv.modules.returns.api.schemas import CustodyHandover, ReturnReceive
from medinv.modules.returns.application.permissions import custody_permission
from medinv.modules.returns.application.service import ReturnService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def receive(
    service: ReturnService, ctx: TenantContext, rid: UUID, body: ReturnReceive, key: str
) -> dict[str, Any]:
    permission = custody_permission(ctx)
    entity = service.return_scope(ctx, rid, permission)

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, permission)
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid})
        version(doc, body.expected_version)
        if (
            doc["workflow_version"] != 2
            or doc["kind"] not in {"SALES", "RETAIL"}
            or doc["state"] != "DRAFT"
        ):
            raise BusinessError("INVALID_STATE", "仅新流程待收货销售退货可登记隔离收货", 409)
        expected = {
            str(r["id"]): (r["base_qty"], r["lot_no"]) for r in service.selected_lines(repo, doc)
        }
        actual = {str(r.source_posting_line_id): (Decimal(r.qty), r.lot_no) for r in body.lines}
        if expected != actual:
            raise BusinessError("RETURN_CHECK_MISMATCH", "收货数量、批号与本次申请不一致", 409)
        repo.execute(
            """INSERT INTO med.return_custody(tenant_id,id,return_id,zone_label,
            note,created_by) VALUES(:t,:id,:r,:zone,:note,:a)""",
            {
                "t": ctx.tenant_id,
                "id": uuid4(),
                "r": rid,
                "zone": body.zone_label,
                "note": body.reason,
                "a": ctx.principal_id,
            },
        )
        repo.execute(
            """UPDATE med.return_document SET state='RECEIVED',version=version+1
            WHERE id=:id""",
            {"id": rid},
        )
        audit(repo, ctx, "return", rid, "returns.received", body.model_dump(mode="json"))
        return {"id": str(rid), "state": "RECEIVED", "version": str(doc["version"] + 1)}

    return command(
        service.uow, ctx, "returns.receive", str(rid), key, body.model_dump(mode="json"), run
    )


def close(repo: SqlSession, ctx: TenantContext, rid: UUID, outcome: str, note: str) -> None:
    receipt = one(repo, "SELECT * FROM med.return_custody WHERE return_id=:id", {"id": rid})
    if repo.rows("SELECT id FROM med.return_custody_close WHERE return_id=:id", {"id": rid}):
        raise BusinessError("CUSTODY_CLOSED", "隔离保管记录已结清", 409)
    repo.execute(
        """INSERT INTO med.return_custody_close(tenant_id,id,return_id,outcome,
        note,created_by) VALUES(:t,:id,:r,:outcome,:note,:a)""",
        {
            "t": ctx.tenant_id,
            "id": uuid4(),
            "r": receipt["return_id"],
            "outcome": outcome,
            "note": note,
            "a": ctx.principal_id,
        },
    )


def hand_back(
    service: ReturnService, ctx: TenantContext, rid: UUID, body: CustodyHandover, key: str
) -> dict[str, Any]:
    permission = custody_permission(ctx)
    entity = service.return_scope(ctx, rid, permission)

    def run(repo: SqlSession) -> dict[str, Any]:
        service.entity(repo, ctx, entity, permission)
        doc = one(repo, "SELECT * FROM med.return_document WHERE id=:id FOR UPDATE", {"id": rid})
        version(doc, body.expected_version)
        if doc["state"] not in ("RECEIVED", "REJECTED", "APPROVED"):
            raise BusinessError("INVALID_STATE", "仅未过账隔离实物可以交还客户", 409)
        close(repo, ctx, rid, "HANDED_BACK", body.reason)
        repo.execute(
            "UPDATE med.return_document SET state='CANCELLED',version=version+1 WHERE id=:id",
            {"id": rid},
        )
        audit(repo, ctx, "return", rid, "returns.handed_back", body.model_dump(mode="json"))
        return {"id": str(rid), "state": "CANCELLED", "version": str(doc["version"] + 1)}

    return command(
        service.uow, ctx, "returns.hand_back", str(rid), key, body.model_dump(mode="json"), run
    )
