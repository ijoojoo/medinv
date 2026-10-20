import csv
import io
from collections import Counter
from typing import Any

from pydantic import ValidationError

from medinv.modules.catalog.api.imports import ProductImport
from medinv.modules.catalog.api.schemas import ProductCreate
from medinv.modules.catalog.application.code_rules import code_lock, current_rule
from medinv.modules.catalog.application.dictionaries import dictionary_lock, validate_dictionary
from medinv.modules.catalog.application.field_policy import policy_lock
from medinv.modules.catalog.application.field_rules import validate_product
from medinv.modules.catalog.application.service import CatalogService
from medinv.platform.database.commands import command
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

COLUMNS = {
    "category": "商品类别",
    "code": "商品编码",
    "name": "名称",
    "manufacturer_name": "生产企业",
    "specification": "规格型号",
    "base_unit": "基本单位",
    "qty_scale": "数量小数位",
    "approval_number": "批准注册备案编号",
    "valid_from": "资质生效日期",
    "valid_until": "资质截止日期",
    "device_class": "器械管理类别",
    "drug_sale_class": "药品销售分类",
    "generic_name": "药品通用名",
    "dosage_form": "剂型",
    "device_model": "器械型号",
    "trade_name": "商品名品牌",
    "search_alias": "检索别名助记码",
    "manufacturer_address": "生产企业地址",
    "storage_conditions": "储存条件",
    "authorization_holder": "上市许可持有人",
    "authorization_holder_address": "上市许可持有人地址",
    "registrant_name": "注册人备案人",
    "registrant_address": "注册人备案人地址",
    "drug_business_scope": "药品经营范围类别",
    "device_business_scope": "器械经营范围类别",
    "quality_note": "质量管控备注",
}
REQUIRED_HEADERS = {
    "商品类别",
    "名称",
    "生产企业",
    "规格型号",
    "基本单位",
    "批准注册备案编号",
    "资质生效日期",
    "资质截止日期",
}
IDENTITY = ("category", "name", "manufacturer_name", "specification", "approval_number")


def parse(content: str) -> list[dict[str, Any]]:
    if len(content.encode("utf-8")) > 262144 or "\0" in content:
        raise BusinessError("IMPORT_FILE_INVALID", "文件超过256 KB或包含无效字符", 422)
    try:
        reader = csv.reader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
        rows = [(reader.line_num, cells) for cells in reader if any(c.strip() for c in cells)]
    except csv.Error as exc:
        raise BusinessError(
            "IMPORT_FILE_INVALID", "CSV引号格式错误，请使用模板另存为CSV UTF-8", 422
        ) from exc
    if not 2 <= len(rows) <= 101:
        raise BusinessError("IMPORT_FILE_INVALID", "每次导入1至100条新商品", 422)

    def cell(value: str) -> str:
        return (value[1:] if value.startswith("'") else value).strip()

    headers = [cell(v) for v in rows[0][1]]
    if (
        len(headers) != len(set(headers))
        or not REQUIRED_HEADERS.issubset(headers)
        or set(headers) - set(COLUMNS.values()) - {"校验结果"}
    ):
        raise BusinessError(
            "IMPORT_HEADERS_INVALID", "表头缺少必需列、重复或不是当前模板，请重新下载模板", 422
        )
    reverse = {v: k for k, v in COLUMNS.items()}
    result = []
    for number, cells in rows[1:]:
        errors = []
        values = {
            reverse[h]: cell(v)
            for h, v in zip(headers, cells, strict=False)
            if h in reverse and cell(v)
        }
        if len(cells) != len(headers):
            errors.append("列数与表头不一致")
        for key, choices in {
            "category": {"药品": "DRUG", "医疗器械": "DEVICE"},
            "device_class": {"第一类": "I", "第二类": "II", "第三类": "III", "待核定": "UNKNOWN"},
            "drug_sale_class": {"处方药": "RX", "非处方药": "OTC", "待核定": "UNKNOWN"},
        }.items():
            if key in values:
                values[key] = choices.get(values[key], values[key])
        raw: dict[str, Any] = dict(values)
        if "qty_scale" in raw and raw["qty_scale"].isascii() and raw["qty_scale"].isdigit():
            raw["qty_scale"] = int(raw["qty_scale"])
        product = None
        try:
            product = ProductCreate(**raw)
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                field = str(error["loc"][0]) if error["loc"] else ""
                errors.append(
                    (COLUMNS.get(field, "商品资料"))
                    + (
                        "必填"
                        if error["type"] == "missing"
                        else "格式或类别不正确，请核对日期、分类、长度及数值"
                    )
                )
        result.append({"row": number, "values": values, "product": product, "errors": errors})
    return result


def lock_policies(repo: SqlSession, ctx: TenantContext) -> None:
    for category in ("DEVICE", "DRUG"):
        policy_lock(repo, ctx, category)
    dictionary_lock(repo, ctx)


def check_rows(repo: SqlSession, ctx: TenantContext, rows: list[dict[str, Any]]) -> None:
    codes = Counter(r["product"].code for r in rows if r["product"] and r["product"].code)
    identities = Counter(
        tuple(getattr(r["product"], k) for k in IDENTITY) for r in rows if r["product"]
    )
    for row in rows:
        p: ProductCreate | None = row["product"]
        if p is None:
            continue
        if p.code and codes[p.code] > 1:
            row["errors"].append("文件内商品编码重复")
        if identities[tuple(getattr(p, k) for k in IDENTITY)] > 1:
            row["errors"].append("文件内同类别、名称、厂家、规格和批准编号重复")
        if p.code and repo.rows(
            "SELECT code FROM med.product WHERE code=:c UNION ALL "
            "SELECT code FROM med.sku WHERE code=:c LIMIT 1",
            {"c": p.code},
        ):
            row["errors"].append("商品编码或历史库存编码已存在；本入口只新增，不覆盖原档案")
        if repo.rows(
            "SELECT id FROM med.product WHERE category=:category AND name=:name "
            "AND manufacturer_name=:manufacturer_name AND specification=:specification "
            "AND approval_number=:approval_number LIMIT 1",
            {k: getattr(p, k) for k in IDENTITY},
        ):
            row["errors"].append("相同类别、名称、厂家、规格和批准编号的商品已存在，请先核对")
        try:
            validate_product(repo, ctx, p.model_dump())
            validate_dictionary(repo, ctx, "base_unit", p.base_unit)
            if not p.code and not current_rule(repo, p.category)["enabled"]:
                row["errors"].append("此类别未启用自动编码，请填写商品编码")
        except BusinessError as exc:
            row["errors"].append(exc.message)


class ProductImports:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def template(self, ctx: TenantContext) -> dict[str, Any]:
        ctx.require("catalog.write")
        return {"headers": list(COLUMNS.values()), "max_rows": 100, "max_bytes": 262144}

    def preview(self, ctx: TenantContext, body: ProductImport) -> dict[str, Any]:
        ctx.require("catalog.write")
        rows = parse(body.content)
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            lock_policies(repo, ctx)
            check_rows(repo, ctx, rows)
        return {
            "rows": [{k: v for k, v in r.items() if k != "product"} for r in rows],
            "can_import": not any(r["errors"] for r in rows),
        }

    def submit(self, ctx: TenantContext, body: ProductImport, key: str) -> dict[str, Any]:
        ctx.require("catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            rows = parse(body.content)
            lock_policies(repo, ctx)
            code_lock(repo, ctx)
            check_rows(repo, ctx, rows)
            invalid = [r for r in rows if r["errors"]]
            if invalid:
                raise BusinessError(
                    "IMPORT_VALIDATION_FAILED",
                    "；".join(f"第 {r['row']} 行：{'、'.join(r['errors'])}" for r in invalid[:5]),
                    422,
                )
            service = CatalogService(self.uow)
            created = [
                {"row": r["row"], **service.insert_product(repo, ctx, r["product"])} for r in rows
            ]
            return {"items": created, "count": len(created), "state": "DRAFT"}

        return command(
            self.uow, ctx, "catalog.product.import", "new", key, body.model_dump(mode="json"), run
        )
