import json
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.inventory.api.transfers import WarehouseCreate
from medinv.modules.inventory.api.warehouses import LocationCreate, WarehouseRename
from medinv.modules.procurement.application.service import encode, one, version
from medinv.modules.settlement.application.service import SettlementService
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


class WarehouseService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow
        self.finance = SettlementService(uow)

    @staticmethod
    def record(repo: SqlSession, target: UUID, lock: bool = False) -> dict[str, Any]:
        row = one(
            repo,
            "SELECT * FROM med.warehouse WHERE id=:id" + (" FOR UPDATE" if lock else ""),
            {"id": target},
        )
        if row["kind"] != "PHYSICAL":
            raise BusinessError("SYSTEM_WAREHOUSE", "系统在途仓由调拨流程维护", 409)
        return row

    def scope(self, ctx: TenantContext, target: UUID, permission: str) -> UUID:
        ctx.require(permission)
        with self.uow.open(ctx, readonly=True) as session:
            row = one(
                SqlSession(session), "SELECT * FROM med.warehouse WHERE id=:id", {"id": target}
            )
            ctx.require(permission, row["entity_id"])
            if row["kind"] != "PHYSICAL":
                raise BusinessError("SYSTEM_WAREHOUSE", "系统在途仓由调拨流程维护", 409)
            return UUID(str(row["entity_id"]))

    def listing(self, ctx: TenantContext, entity: UUID, search: str, offset: int) -> dict[str, Any]:
        ctx.require("inventory.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """SELECT w.*,w.version::text AS version,count(*) OVER() AS total,
                (SELECT count(*) FROM med.location l WHERE
                  (l.tenant_id,l.warehouse_id)=(w.tenant_id,w.id)) AS location_count
                FROM med.warehouse w WHERE entity_id=:e AND kind='PHYSICAL' AND archived_at IS NULL
                  AND (code ILIKE :q OR name ILIKE :q)
                ORDER BY code,id LIMIT 50 OFFSET :offset""",
                {"e": entity, "q": "%" + search + "%", "offset": offset},
            )
            return {
                "items": encode(rows),
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def detail(
        self,
        ctx: TenantContext,
        target: UUID,
        search: str = "",
        offset: int = 0,
        history_offset: int = 0,
    ) -> dict[str, Any]:
        self.scope(ctx, target, "inventory.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = self.record(repo, target)
            row["version"] = str(row["version"])
            locations = repo.rows(
                """SELECT l.*,coalesce(l.name,l.code) AS name,l.version::text AS version,
                count(*) OVER() AS total FROM med.location l WHERE warehouse_id=:id
                  AND (code ILIKE :q OR name ILIKE :q)
                ORDER BY code,id LIMIT 50 OFFSET :offset""",
                {"id": target, "q": "%" + search + "%", "offset": offset},
            )
            history = repo.rows(
                """SELECT id,created_by,created_at,action,reason,count(*) OVER() AS total
                FROM med.audit_event WHERE resource_type='warehouse' AND resource_id=:id
                ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset""",
                {"id": target, "offset": history_offset},
            )
            return dict(
                encode(
                    {
                        "warehouse": row,
                        "locations": locations,
                        "has_more": bool(
                            locations and offset + len(locations) < locations[0]["total"]
                        ),
                        "history": [
                            {
                                **{k: r[k] for k in ("id", "created_at", "created_by", "action")},
                                "detail": json.loads(r["reason"]),
                            }
                            for r in history
                        ],
                        "history_has_more": bool(
                            history and history_offset + len(history) < history[0]["total"]
                        ),
                    }
                )
            )

    def create(self, ctx: TenantContext, body: WarehouseCreate, key: str) -> dict[str, Any]:
        ctx.require("warehouses.write", body.entity_id)

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, body.entity_id, "warehouses.write")
            if body.code.upper().startswith("SYS-"):
                raise BusinessError("WAREHOUSE_CODE_RESERVED", "SYS- 为系统仓库保留前缀", 422)
            if repo.rows(
                "SELECT id FROM med.warehouse WHERE entity_id=:e AND code=:c",
                {"e": body.entity_id, "c": body.code},
            ):
                raise BusinessError("WAREHOUSE_CODE_EXISTS", "仓库编码已存在", 409)
            wid, lid = uuid4(), uuid4()
            params = {
                "t": ctx.tenant_id,
                "a": ctx.principal_id,
                "w": wid,
                "l": lid,
                "e": body.entity_id,
                "code": body.code,
                "name": body.name,
                "loc": body.location_code,
            }
            repo.execute(
                """INSERT INTO med.warehouse
              (tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
              VALUES(:t,:w,:a,:a,:e,:code,'PHYSICAL',:name)""",
                params,
            )
            repo.execute(
                """INSERT INTO med.location
              (tenant_id,id,created_by,updated_by,warehouse_id,code,name)
              VALUES(:t,:l,:a,:a,:w,:loc,:loc)""",
                params,
            )
            result = {"id": str(wid), "location_id": str(lid)}
            audit(
                repo,
                ctx,
                "warehouse",
                wid,
                "inventory.warehouse_created",
                {**body.model_dump(mode="json"), **result},
            )
            return result

        return self.finance.permanent(
            ctx,
            "warehouses.create",
            str(body.entity_id),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    @staticmethod
    def touch(repo: SqlSession, ctx: TenantContext, target: UUID) -> None:
        repo.execute(
            """UPDATE med.warehouse SET version=version+1,
          updated_at=clock_timestamp(),updated_by=:a WHERE id=:id""",
            {"id": target, "a": ctx.principal_id},
        )

    def add_location(
        self, ctx: TenantContext, target: UUID, body: LocationCreate, key: str
    ) -> dict[str, Any]:
        entity = self.scope(ctx, target, "warehouses.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "warehouses.write")
            warehouse = self.record(repo, target, lock=True)
            version(warehouse, body.expected_version)
            if repo.rows(
                "SELECT id FROM med.location WHERE warehouse_id=:w AND code=:c",
                {"w": target, "c": body.code},
            ):
                raise BusinessError("LOCATION_CODE_EXISTS", "该仓库已存在相同库位编码", 409)
            lid = uuid4()
            repo.execute(
                """INSERT INTO med.location
              (tenant_id,id,created_by,updated_by,warehouse_id,code,name)
              VALUES(:t,:id,:a,:a,:w,:code,:name)""",
                {
                    "t": ctx.tenant_id,
                    "id": lid,
                    "a": ctx.principal_id,
                    "w": target,
                    "code": body.code,
                    "name": body.name,
                },
            )
            self.touch(repo, ctx, target)
            result = {
                "id": str(lid),
                "warehouse_id": str(target),
                "version": "1",
                "warehouse_version": str(warehouse["version"] + 1),
            }
            audit(
                repo,
                ctx,
                "warehouse",
                target,
                "inventory.location_created",
                {**body.model_dump(mode="json"), **result},
            )
            return result

        return self.finance.permanent(
            ctx,
            "warehouses.location.create",
            str(target),
            key,
            body.client_operation_id,
            body.model_dump(mode="json"),
            run,
        )

    def rename(
        self,
        ctx: TenantContext,
        target: UUID,
        body: WarehouseRename,
        key: str,
        location: UUID | None = None,
    ) -> dict[str, Any]:
        entity = self.scope(ctx, target, "warehouses.write")
        # Check the child before looking up an idempotent response as well.
        if location is not None:
            with self.uow.open(ctx, readonly=True) as session:
                one(
                    SqlSession(session),
                    "SELECT id FROM med.location WHERE id=:id AND warehouse_id=:w",
                    {"id": location, "w": target},
                )

        def run(repo: SqlSession) -> dict[str, Any]:
            self.finance.entity(repo, ctx, entity, "warehouses.write")
            warehouse = self.record(repo, target, lock=True)
            row = (
                warehouse
                if location is None
                else one(
                    repo,
                    "SELECT *,coalesce(name,code) AS name FROM med.location "
                    "WHERE id=:id AND warehouse_id=:w FOR UPDATE",
                    {"id": location, "w": target},
                )
            )
            version(row, body.expected_version)
            if row["name"] == body.name:
                raise BusinessError("WAREHOUSE_UNCHANGED", "名称没有变化", 422)
            table = "warehouse" if location is None else "location"
            repo.execute(
                f"""UPDATE med.{table} SET name=:name,version=version+1,
              updated_at=clock_timestamp(),updated_by=:a WHERE id=:id""",
                {"name": body.name, "a": ctx.principal_id, "id": location or target},
            )
            if location is not None:
                self.touch(repo, ctx, target)
            result = {
                "id": str(location or target),
                "version": str(row["version"] + 1),
                "warehouse_version": str(warehouse["version"] + 1),
            }
            audit(
                repo,
                ctx,
                "warehouse",
                target,
                f"inventory.{table}_renamed",
                {
                    "before": row["name"],
                    "after": body.name,
                    "code": row["code"],
                    "reason": body.reason,
                    **result,
                },
            )
            return result

        return command(
            self.uow,
            ctx,
            "warehouses.rename" if location is None else "locations.rename",
            f"{target}:{location}" if location else str(target),
            key,
            body.model_dump(mode="json"),
            run,
        )
