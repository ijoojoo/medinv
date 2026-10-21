"""Read-only inspection reporting and bounded, consistent CSV exports."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from pydantic import Field, StringConstraints, model_validator

from medinv.modules.catalog.api.schemas import DTO
from medinv.modules.workspace.management import require_entity
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

ZONE = ZoneInfo("Asia/Shanghai")
PAGE_SIZE = 50
EXPORT_LIMIT = 10000
Search = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class QualityRecordQuery(DTO):
    entity_id: UUID
    search: Search = ""
    supplier: Search = ""
    product: Search = ""
    lot_no: Search = ""
    warehouse_id: UUID | None = None
    result: Literal["", "QUALIFIED", "MIXED", "REJECTED", "HAS_REJECTED"] = ""
    date_from: date | None = None
    date_to: date | None = None
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def valid_dates(self) -> QualityRecordQuery:
        if self.date_to == date.max or (
            self.date_from and self.date_to and self.date_from > self.date_to
        ):
            raise ValueError("日期范围无效，请检查起止日期")
        return self


# Order content cannot change after receipt. Supplier/warehouse names come from the
# corresponding approval evidence, never from today's master data.
BASE = """WITH records AS (
 SELECT l.id,i.id AS inspection_id,i.inspected_at,i.created_by,i.note,
   o.id AS order_id,o.number AS order_number,o.revision_no AS order_revision,
   r.id AS receipt_id,r.number AS receipt_number,r.shipment_ref,
   o.supplier_id,o.warehouse_id,rl.sku_id,ol.line_no,
   coalesce(nullif(a.value#>>'{order,supplier_name}',''),'历史未留存') AS party_name,
   coalesce(nullif(a.value#>>'{order,warehouse_name}',''),'历史未留存') AS warehouse_name,
   coalesce(nullif(ol.document_snapshot#>>'{values,product}',''),'历史未留存') AS product_name,
   coalesce(ol.document_snapshot#>>'{values,code}','') AS product_code,
   coalesce(ol.document_snapshot#>>'{values,specification}','') AS specification,
   coalesce(ol.document_snapshot#>>'{values,manufacturer_name}','') AS manufacturer_name,
   coalesce(ol.document_snapshot#>>'{values,approval_number}','') AS approval_number,
   coalesce(nullif(ol.document_snapshot#>>'{values,base_unit}',''),'历史未留存') AS base_unit,
   (nullif(a.value#>>'{order,supplier_name}','') IS NOT NULL AND
    nullif(ol.document_snapshot#>>'{values,product}','') IS NOT NULL AND
    nullif(ol.document_snapshot#>>'{values,base_unit}','') IS NOT NULL) AS history_complete,
   lot.lot_no,lot.mfg_date,lot.raw_expiry AS expiry_date,
   l.qualified_qty,l.rejected_qty,
   CASE WHEN l.rejected_qty=0 THEN 'QUALIFIED'
        WHEN l.qualified_qty=0 THEN 'REJECTED' ELSE 'MIXED' END AS result
 FROM med.inspection i
 JOIN med.receipt r ON (r.tenant_id,r.id)=(i.tenant_id,i.receipt_id)
 JOIN med.purchase_order o ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
 JOIN med.inspection_line l ON (l.tenant_id,l.inspection_id)=(i.tenant_id,i.id)
 JOIN med.receipt_line rl ON (rl.tenant_id,rl.id)=(l.tenant_id,l.receipt_line_id)
 JOIN med.purchase_order_line ol ON (ol.tenant_id,ol.id)=(rl.tenant_id,rl.order_line_id)
 JOIN med.lot lot ON (lot.tenant_id,lot.id)=(rl.tenant_id,rl.lot_id)
 LEFT JOIN LATERAL (
   SELECT ae.reason::jsonb->'agreement' AS value FROM med.audit_event ae
   WHERE ae.tenant_id=o.tenant_id AND ae.resource_type='purchase_order'
     AND ae.resource_id=o.id AND ae.action='purchase.approved'
     AND ae.reason::jsonb->>'revision_no'=o.revision_no::text
     AND ae.created_at<=i.inspected_at
   ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1
 ) a ON TRUE
 WHERE o.entity_id=:e
), filtered AS (SELECT * FROM records v WHERE TRUE {filters})
"""


def query_sql(query: QualityRecordQuery) -> tuple[str, dict[str, Any]]:
    filters, params = document_filters(
        "v",
        "concat_ws(' ',v.order_number,v.receipt_number,v.shipment_ref,v.party_name,"
        "v.product_name,v.product_code,v.lot_no,v.note)",
        {
            "QUALIFIED": "v.result='QUALIFIED'",
            "REJECTED": "v.result='REJECTED'",
            "MIXED": "v.result='MIXED'",
            "HAS_REJECTED": "v.rejected_qty>0",
        },
        query.search,
        query.result,
        query.date_from,
        query.date_to,
        date_column="inspected_at",
    )
    for key, expression in (
        ("supplier", "v.party_name"),
        ("product", "concat_ws(' ',v.product_name,v.product_code,v.specification)"),
        ("lot_no", "v.lot_no"),
    ):
        value = getattr(query, key)
        if value:
            filters += f" AND strpos(lower({expression}),lower(:{key}))>0"
            params[key] = value
    if query.warehouse_id:
        filters += " AND v.warehouse_id=:warehouse"
        params["warehouse"] = query.warehouse_id
    return BASE.replace("{filters}", filters), {**params, "e": query.entity_id}


RESULTS = {"QUALIFIED": "全部合格", "MIXED": "部分拒收", "REJECTED": "全部拒收"}
CSV_COLUMNS = [
    ("inspected_at", "验收时间（北京时间）"),
    ("order_number", "采购单号"),
    ("order_revision", "采购内容版本"),
    ("receipt_number", "收货单号"),
    ("shipment_ref", "随货单号"),
    ("party_name", "供应商（审核时）"),
    ("warehouse_name", "仓库（审核时）"),
    ("product_code", "商品编码（订购时）"),
    ("product_name", "商品名称（订购时）"),
    ("specification", "规格型号"),
    ("manufacturer_name", "生产企业"),
    ("approval_number", "批准文号/注册备案号"),
    ("lot_no", "批号"),
    ("mfg_date", "生产日期"),
    ("expiry_date", "有效期至"),
    ("base_unit", "基本单位"),
    ("qualified_qty", "合格数量"),
    ("rejected_qty", "拒收数量"),
    ("result", "本次行验收结果"),
    ("note", "验收意见"),
    ("created_by", "验收人账号标识"),
    ("inspection_id", "验收记录标识"),
    ("id", "验收明细标识"),
    ("history_complete", "关键历史信息"),
]


def csv_cell(value: Any) -> str:
    result = "" if value is None else str(value)
    # Match the existing CSV conventions: preserve identifiers/decimal precision,
    # and neutralize formulas including whitespace and full-width prefixes.
    if result and (result[0].isspace() or result[0] in "'0123456789=+-@＝＋－＠"):
        result = "'" + result
    return result


class QualityRecords:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def listing(self, ctx: TenantContext, query: QualityRecordQuery) -> dict[str, Any]:
        ctx.require("quality.read", query.entity_id)
        sql, params = query_sql(query)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            require_entity(repo, query.entity_id)
            rows = repo.rows(
                sql
                + """SELECT * FROM filtered
                  ORDER BY inspected_at DESC,inspection_id DESC,id LIMIT 51 OFFSET :offset""",
                {**params, "offset": query.offset},
            )
            counts = repo.rows(
                sql
                + """SELECT count(*) AS total,count(DISTINCT inspection_id) AS inspections,
                  count(*) FILTER(WHERE NOT history_complete) AS incomplete FROM filtered""",
                params,
            )[0]
            totals = repo.rows(
                sql
                + """SELECT base_unit,sum(qualified_qty)::text AS qualified_qty,
                  sum(rejected_qty)::text AS rejected_qty FROM filtered
                  GROUP BY base_unit ORDER BY base_unit""",
                params,
            )
            warehouses = repo.rows(
                """SELECT id,name,archived_at IS NOT NULL AS archived FROM med.warehouse
                   WHERE entity_id=:e AND kind='PHYSICAL' ORDER BY name,id""",
                {"e": query.entity_id},
            )
            return dict(
                jsonable_encoder(
                    {
                        "items": rows[:PAGE_SIZE],
                        "has_more": len(rows) > PAGE_SIZE,
                        **counts,
                        "totals": totals,
                        "warehouses": warehouses,
                        "can_open_source": "purchase.read" in ctx.permissions,
                        "export_limit": EXPORT_LIMIT,
                        "generated_at": repo.rows("SELECT transaction_timestamp() AS value", {})[0][
                            "value"
                        ],
                    },
                    custom_encoder={Decimal: lambda d: format(d, "f")},
                )
            )

    def export(self, ctx: TenantContext, query: QualityRecordQuery) -> tuple[str, str, int]:
        ctx.require("quality.read", query.entity_id)
        sql, params = query_sql(query)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            require_entity(repo, query.entity_id)
            rows = repo.rows(
                sql
                + """SELECT * FROM filtered
                  ORDER BY inspected_at DESC,inspection_id DESC,id LIMIT :limit""",
                {**params, "limit": EXPORT_LIMIT + 1},
            )
            if len(rows) > EXPORT_LIMIT:
                raise BusinessError(
                    "EXPORT_TOO_LARGE", "验收明细超过10000行，请缩小日期或商品范围后导出", 422
                )
        output = io.StringIO(newline="")
        writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerow([label for _, label in CSV_COLUMNS])
        for row in rows:
            row["inspected_at"] = row["inspected_at"].astimezone(ZONE).isoformat(timespec="seconds")
            row["result"] = RESULTS[row["result"]]
            row["history_complete"] = "已留存" if row["history_complete"] else "部分历史未留存"
            writer.writerow([csv_cell(row[key]) for key, _ in CSV_COLUMNS])
        stamp = datetime.now(ZONE).strftime("%Y%m%d-%H%M%S")
        return "\ufeff" + output.getvalue(), f"采购验收记录-{stamp}.csv", len(rows)
