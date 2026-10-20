"""Inline basic-unit fields, sharing the existing code/price records and audit."""

from typing import Any
from uuid import UUID, uuid4

from medinv.modules.catalog.api.master import MasterSave
from medinv.modules.catalog.application.codes import lock_codes
from medinv.modules.catalog.application.prices import ReferencePrices
from medinv.modules.catalog.domain.master_schema import presentation
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def check_trade_fields(
    ctx: TenantContext,
    schema: dict[str, Any],
    profile: str,
    values: dict[str, Any],
    body: MasterSave,
) -> None:
    definitions = {f["key"]: f for f in presentation(schema, profile, values)}
    for key, change in (
        ("barcodes", body.basic_unit_barcode),
        ("reference_prices", body.reference_prices),
    ):
        if change is None:
            continue
        field = definitions[key]
        if not field["applicable"]:
            raise BusinessError(
                "MASTER_TRADE_INAPPLICABLE", "当前品类不适用商品条码或库存商品参考价", 422
            )
        if (
            field["read_only"]
            or field["lifecycle"] == "INACTIVE"
            or field["write_permission"] not in ctx.permissions
            or field["read_permission"] not in ctx.permissions
        ):
            raise BusinessError("MASTER_TRADE_READONLY", "条码或参考价格没有修改权限或已停用", 403)


def write_trade_fields(
    repo: SqlSession, ctx: TenantContext, product: UUID, body: MasterSave
) -> None:
    if body.basic_unit_barcode is None and body.reference_prices is None:
        return
    skus = repo.rows("SELECT id FROM med.sku WHERE product_id=:p", {"p": product})
    if len(skus) != 1:
        raise BusinessError("MASTER_TRADE_TARGET", "请按商品规格分别维护条码和价格", 422)
    sku = skus[0]["id"]
    change = body.basic_unit_barcode
    if change is not None:
        lock_codes(repo, ctx, "skus", sku)
        rows = repo.rows(
            "SELECT * FROM med.catalog_code WHERE sku_id=:s AND scheme='BARCODE' "
            "AND packaging_id IS NULL AND state='ACTIVE' "
            "ORDER BY created_at DESC,id DESC FOR UPDATE",
            {"s": sku},
        )
        expected = [(str(r.id), r.version) for r in change.expected_entries]
        if sorted(expected) != sorted((str(r["id"]), str(r["version"])) for r in rows):
            raise BusinessError(
                "BARCODE_VERSION_CONFLICT", "基本单位条码已变化，输入已保留，请重新读取后核对", 409
            )
        previous = rows[0] if rows else None
        if change.value != (previous["external_code"] if previous else None):
            if previous:
                repo.execute(
                    "UPDATE med.catalog_code SET state='INACTIVE',version=version+1,"
                    "updated_by=:a,updated_at=clock_timestamp() WHERE id=:id",
                    {"a": ctx.principal_id, "id": previous["id"]},
                )
                audit(
                    repo,
                    ctx,
                    "catalog_code",
                    previous["id"],
                    "catalog_code.state",
                    {
                        "state": "INACTIVE",
                        "previous_state": "ACTIVE",
                        "reason": body.reason,
                        "external_code": previous["external_code"],
                        "scheme": "BARCODE",
                    },
                )
            if change.value:
                cid = uuid4()
                inserted = repo.rows(
                    "INSERT INTO med.catalog_code(tenant_id,id,sku_id,scheme,namespace,"
                    "external_code,"
                    "evidence_ref,created_by,updated_by) VALUES(:t,:id,:s,'BARCODE','',:code,"
                    "'商品资料直接录入',:a,:a) ON CONFLICT DO NOTHING RETURNING id",
                    {
                        "t": ctx.tenant_id,
                        "id": cid,
                        "s": sku,
                        "code": change.value,
                        "a": ctx.principal_id,
                    },
                )
                if not inserted:
                    raise BusinessError(
                        "EXTERNAL_CODE_EXISTS",
                        "此条码已有启用记录，请核对商品和包装；本次修改未保存",
                        409,
                    )
                audit(
                    repo,
                    ctx,
                    "catalog_code",
                    cid,
                    "catalog_code.created",
                    {
                        "scheme": "BARCODE",
                        "external_code": change.value,
                        "target_kind": "skus",
                        "target_id": str(sku),
                        "packaging_id": None,
                        "reason": body.reason,
                        "evidence_ref": "商品资料直接录入",
                    },
                )
    if body.reference_prices is not None:
        ReferencePrices.write(repo, ctx, sku, body.reference_prices, allow_unchanged=True)
