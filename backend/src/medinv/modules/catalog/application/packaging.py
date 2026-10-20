from decimal import Decimal
from fractions import Fraction
from typing import Any
from uuid import UUID, uuid4

from medinv.modules.catalog.api.packaging import PackagingCreate
from medinv.modules.catalog.api.schemas import Review
from medinv.modules.inventory.domain.decimal_values import quantity
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def one(repo: SqlSession, sql: str, **params: Any) -> dict[str, Any]:
    rows = repo.rows(sql, params)
    if not rows:
        raise BusinessError("NOT_FOUND", "包装或商品不可见", 404)
    return rows[0]


def convert(raw: str, numerator: int, denominator: int, scale: int) -> Decimal:
    entered = quantity(raw, sku_scale=6)
    scaled = Fraction(entered) * numerator * 10**scale / denominator
    if scaled.denominator != 1:
        raise ValueError("conversion is not exactly representable in base units")
    return quantity(format(Decimal(scaled.numerator).scaleb(-scale), "f"), sku_scale=scale)


def trade(
    repo: SqlSession, sku: dict[str, Any], packaging_id: UUID | None, raw: str
) -> tuple[Decimal, dict[str, Any]]:
    snapshot: dict[str, Any] = {
        "base_unit": sku["base_unit"],
        "qty_scale": sku["qty_scale"],
        "unit_name": sku["base_unit"],
        "numerator": 1,
        "denominator": 1,
    }
    if packaging_id is not None:
        row = one(
            repo,
            "SELECT * FROM med.packaging WHERE id=:id AND sku_id=:s FOR SHARE",
            id=packaging_id,
            s=sku["id"],
        )
        if row["state"] != "APPROVED" or row["effective_until"] is not None:
            raise BusinessError("PACKAGING_UNAVAILABLE", "包装未审核或已停用，请刷新选择", 409)
        snapshot.update({k: row[k] for k in ("unit_name", "numerator", "denominator", "revision")})
        snapshot["packaging_id"] = str(row["id"])
    try:
        qty = convert(raw, snapshot["numerator"], snapshot["denominator"], sku["qty_scale"])
    except ValueError as exc:
        raise BusinessError(
            "INVALID_QUANTITY", "换算后数量超限或不符合基本单位精度，不能四舍五入", 422
        ) from exc
    snapshot["trade_qty"] = raw
    snapshot["base_qty"] = format(qty, "f")
    return qty, snapshot


class PackagingService:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(
        self, ctx: TenantContext, sku: UUID, offset: int = 0, approved_only: bool = False
    ) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            record = one(repo, "SELECT * FROM med.sku WHERE id=:id", id=sku)
            rows = repo.rows(
                """SELECT *,count(*) OVER() AS total FROM med.packaging
                WHERE sku_id=:s AND (NOT :approved OR state='APPROVED')
                ORDER BY created_at DESC,id LIMIT 50 OFFSET :offset""",
                {"s": sku, "offset": offset, "approved": approved_only},
            )
            # Only JSON-safe scalars are exposed; factor values remain exact decimal strings.
            return {
                "sku_id": str(sku),
                "base_unit": record["base_unit"],
                "sku_version": str(record["version"]),
                "items": [
                    {
                        **{
                            k: str(r[k])
                            for k in ("id", "version", "numerator", "denominator", "created_by")
                        },
                        **{k: r[k] for k in ("unit_name", "revision", "state")},
                        "reviewed_by": str(r["reviewed_by"]) if r["reviewed_by"] else None,
                        "created_at": r["created_at"].isoformat(),
                    }
                    for r in rows
                ],
                "has_more": bool(rows and offset + len(rows) < rows[0]["total"]),
            }

    def create(
        self, ctx: TenantContext, sku: UUID, body: PackagingCreate, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            row = one(repo, "SELECT * FROM med.sku WHERE id=:id FOR UPDATE", id=sku)
            if str(row["version"]) != body.expected_sku_version:
                raise BusinessError("VERSION_CONFLICT", "包装列表已变化，请刷新", 409)
            if body.unit_name == row["base_unit"]:
                raise BusinessError("BASE_UNIT_RESERVED", "基本单位换算固定为 1，不需另建包装", 422)
            if repo.rows(
                "SELECT id FROM med.packaging WHERE sku_id=:s AND unit_name=:u AND state='DRAFT'",
                {"s": sku, "u": body.unit_name},
            ):
                raise BusinessError("PACKAGING_DRAFT_EXISTS", "该单位已有待审核版本", 409)
            ratio = Fraction(int(body.numerator), int(body.denominator))
            revision = one(
                repo,
                "SELECT coalesce(max(revision),0)+1 AS n FROM med.packaging "
                "WHERE sku_id=:s AND unit_name=:u",
                s=sku,
                u=body.unit_name,
            )["n"]
            pid = uuid4()
            repo.execute(
                """INSERT INTO med.packaging(tenant_id,id,created_by,updated_by,
              sku_id,unit_name,numerator,denominator,revision,effective_from)
              VALUES(:t,:id,:a,:a,:s,:unit,:n,:d,:r,clock_timestamp())""",
                {
                    "t": ctx.tenant_id,
                    "id": pid,
                    "a": ctx.principal_id,
                    "s": sku,
                    "unit": body.unit_name,
                    "n": ratio.numerator,
                    "d": ratio.denominator,
                    "r": revision,
                },
            )
            self.touch(repo, ctx, sku)
            result = {"id": str(pid), "version": "1", "state": "DRAFT", "revision": revision}
            audit(
                repo,
                ctx,
                "packaging",
                pid,
                "catalog.packaging_created",
                {**body.model_dump(), **result},
            )
            return result

        return command(
            self.uow,
            ctx,
            "catalog.packaging.create",
            str(sku),
            key,
            body.model_dump(mode="json"),
            run,
        )

    @staticmethod
    def touch(repo: SqlSession, ctx: TenantContext, sku: UUID) -> None:
        repo.execute(
            "UPDATE med.sku SET version=version+1,updated_at=clock_timestamp(),updated_by=:a "
            "WHERE id=:s",
            {"a": ctx.principal_id, "s": sku},
        )

    def review(
        self, ctx: TenantContext, sku: UUID, target: UUID, body: Review, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.review")
        with self.uow.open(ctx, readonly=True) as session:
            one(
                SqlSession(session),
                "SELECT id FROM med.packaging WHERE id=:id AND sku_id=:s",
                id=target,
                s=sku,
            )

        def run(repo: SqlSession) -> dict[str, Any]:
            one(repo, "SELECT id FROM med.sku WHERE id=:s FOR UPDATE", s=sku)
            row = one(
                repo,
                "SELECT * FROM med.packaging WHERE id=:id AND sku_id=:s FOR UPDATE",
                id=target,
                s=sku,
            )
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "包装版本已变化，请刷新", 409)
            if row["state"] == "RETIRED" or (
                body.decision == "APPROVE" and row["state"] != "DRAFT"
            ):
                raise BusinessError("INVALID_STATE", "该包装版本不能执行此操作", 409)
            if body.decision == "APPROVE" and row["created_by"] == ctx.principal_id:
                raise BusinessError("SEPARATION_REQUIRED", "包装建档人与审核人必须不同", 403)
            state = "APPROVED" if body.decision == "APPROVE" else "RETIRED"
            if state == "APPROVED":
                active_count = one(
                    repo,
                    "SELECT count(*) AS n FROM med.packaging WHERE sku_id=:s "
                    "AND state='APPROVED' AND unit_name<>:u",
                    s=sku,
                    u=row["unit_name"],
                )["n"]
                if active_count >= 20:
                    raise BusinessError("PACKAGING_LIMIT", "每个商品最多启用 20 个包装单位", 422)
                previous = repo.rows(
                    "SELECT id FROM med.packaging WHERE sku_id=:s AND unit_name=:u "
                    "AND state='APPROVED' FOR UPDATE",
                    {"s": sku, "u": row["unit_name"]},
                )
                for prior in previous:
                    self.retire(repo, ctx, prior["id"])
                    audit(
                        repo,
                        ctx,
                        "packaging",
                        prior["id"],
                        "catalog.packaging_replaced",
                        {"replacement_id": str(target), **body.model_dump()},
                    )
                repo.execute(
                    """UPDATE med.packaging SET state='APPROVED',reviewed_by=:a,
                  reviewed_at=clock_timestamp(),updated_by=:a,
                  updated_at=clock_timestamp(),version=version+1
                  WHERE id=:id""",
                    {"a": ctx.principal_id, "id": target},
                )
            else:
                self.retire(repo, ctx, target)
            self.touch(repo, ctx, sku)
            audit(repo, ctx, "packaging", target, "catalog.packaging_reviewed", body.model_dump())
            return {"id": str(target), "version": str(row["version"] + 1), "state": state}

        return command(
            self.uow,
            ctx,
            "catalog.packaging.review",
            f"{sku}:{target}",
            key,
            body.model_dump(mode="json"),
            run,
        )

    @staticmethod
    def retire(repo: SqlSession, ctx: TenantContext, target: UUID) -> None:
        repo.execute(
            """UPDATE med.packaging SET state='RETIRED',effective_until=clock_timestamp(),
          updated_by=:a,updated_at=clock_timestamp(),version=version+1 WHERE id=:id""",
            {"id": target, "a": ctx.principal_id},
        )
