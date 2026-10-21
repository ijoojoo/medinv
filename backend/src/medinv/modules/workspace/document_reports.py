"""Read-only document reports: one fact grain per kind, shared filters and exact totals."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import Field, StringConstraints, model_validator

from medinv.modules.catalog.api.schemas import DTO
from medinv.modules.workspace.management import require_entity
from medinv.modules.workspace.quality_records import ZONE, csv_cell
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.document_query import document_filters
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Kind = Literal["purchase", "receipt", "inspection", "inbound", "sale", "outbound"]
KINDS = {
    "purchase": "采购订单",
    "receipt": "采购收货",
    "inspection": "采购验收",
    "inbound": "采购入库",
    "sale": "销售订单",
    "outbound": "销售出库",
}
Search = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
PAGE_SIZE = 50
EXPORT_LIMIT = 10000


class ReportQuery(DTO):
    entity_id: UUID
    view: Literal["summary", "detail"] = "summary"
    search: Search = ""
    party: Search = ""
    product: Search = ""
    warehouse: Search = ""
    lot_no: Search = ""
    state: Search = ""
    date_from: date | None = None
    date_to: date | None = None
    document_id: UUID | None = None
    order_id: UUID | None = None
    offset: int = Field(default=0, ge=0, le=100000)
    dimension: Literal["day", "month", "party", "warehouse", "product", "state"] = "day"
    metric: Literal["gross_amount", "net_amount", "tax_amount", "documents", "lines", "qty"] = (
        "gross_amount"
    )
    unit: Search = ""
    columns: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_dates(self) -> ReportQuery:
        if self.date_to == date.max or (
            self.date_from and self.date_to and self.date_from > self.date_to
        ):
            raise ValueError("日期范围无效，请检查起止日期")
        return self


def permission(ctx: TenantContext, kind: Kind, entity: UUID) -> bool:
    required = "sales.read" if kind in ("sale", "outbound") else "purchase.read"
    if kind == "inspection" and required not in ctx.permissions:
        ctx.require("quality.read", entity)
        return False
    ctx.require(required, entity)
    return True


def source_sql(kind: Kind) -> str:
    sale = kind in ("sale", "outbound")
    prefix, party_key = ("sales", "customer") if sale else ("purchase", "supplier")
    status = (
        "o.state"
        if sale
        else (
            "CASE WHEN o.approval_state='APPROVED' THEN o.fulfillment_state "
            "ELSE o.approval_state END"
        )
    )
    draft = "o.state='DRAFT'" if sale else "o.approval_state='DRAFT'"
    joins = ""
    identifier, number, occurred, line = "o.id", "o.number", "o.created_at", "ol.id"
    qty, gross, net = (
        "ol.qty",
        "coalesce(ol.gross_amount,round(ol.qty*ol.unit_price,2))",
        "coalesce(ol.net_amount,round(round(ol.qty*ol.unit_price,2)/(1+ol.tax_rate),2))",
    )
    qualified = rejected = "NULL::numeric"
    lot, mfg, expiry, note = "''::text", "NULL::date", "NULL::date", "''::text"
    condition = "TRUE"
    if kind in ("receipt", "inspection", "inbound"):
        joins = """JOIN med.receipt_line rl ON
          (rl.tenant_id,rl.order_line_id)=(ol.tenant_id,ol.id)
          JOIN med.receipt r ON (r.tenant_id,r.id)=(rl.tenant_id,rl.receipt_id)
          JOIN med.lot lot ON (lot.tenant_id,lot.id)=(rl.tenant_id,rl.lot_id)"""
        identifier, number, occurred, line = "r.id", "r.number", "r.received_at", "rl.id"
        qty, status, note = "rl.qty", "'RECEIVED'::text", "r.transport_note"
        lot, mfg, expiry = "lot.lot_no", "lot.mfg_date", "lot.raw_expiry"
        if kind in ("inspection", "inbound"):
            joins += """ JOIN med.inspection_line il ON
              (il.tenant_id,il.receipt_line_id)=(rl.tenant_id,rl.id)
              JOIN med.inspection i ON (i.tenant_id,i.id)=(il.tenant_id,il.inspection_id)"""
            identifier, number, occurred, line = (
                "i.id",
                "'YS-'||replace(i.id::text,'-','')",
                "i.inspected_at",
                "il.id",
            )
            qty, qualified, rejected, note = (
                "il.qualified_qty+il.rejected_qty",
                "il.qualified_qty",
                "il.rejected_qty",
                "i.note",
            )
            status = (
                "CASE WHEN il.rejected_qty=0 THEN 'QUALIFIED' "
                "WHEN il.qualified_qty=0 THEN 'REJECTED' ELSE 'MIXED' END"
            )
        gross = f"round(coalesce(ol.gross_amount,round(ol.qty*ol.unit_price,2))*({qty})/ol.qty,2)"
        net = f"round(({gross})/(1+ol.tax_rate),2)"
        if kind == "inbound":
            joins += """ JOIN med.receipt_posting rp ON
              (rp.tenant_id,rp.inspection_id)=(i.tenant_id,i.id)
              JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(rp.tenant_id,rp.posting_id)
              JOIN med.posting_line pl ON
                (pl.tenant_id,pl.posting_id,pl.source_line_id)=(pb.tenant_id,pb.id,il.id)"""
            identifier, number, occurred, line = (
                "rp.id",
                "'RK-'||replace(rp.id::text,'-','')",
                "pb.posted_at",
                "pl.id",
            )
            qty, gross, net, status = (
                "pl.base_qty",
                "pl.gross_amount",
                "pl.net_amount",
                "'POSTED'::text",
            )
            qualified = rejected = "NULL::numeric"
            condition = "rp.state='POSTED'"
    elif kind == "outbound":
        joins = """JOIN med.reservation_allocation ra ON
          (ra.tenant_id,ra.source_line_id)=(ol.tenant_id,ol.id)
          JOIN med.shipment_line sl ON (sl.tenant_id,sl.allocation_id)=(ra.tenant_id,ra.id)
          JOIN med.shipment sh ON (sh.tenant_id,sh.id)=(sl.tenant_id,sl.shipment_id)
          JOIN med.posting_batch pb ON (pb.tenant_id,pb.id)=(sh.tenant_id,sh.posting_id)
          JOIN med.posting_line pl ON
            (pl.tenant_id,pl.posting_id,pl.source_line_id)=(pb.tenant_id,pb.id,sl.id)
          JOIN med.stock_balance sb ON (sb.tenant_id,sb.id)=(ra.tenant_id,ra.balance_id)
          JOIN med.lot lot ON (lot.tenant_id,lot.id)=(sb.tenant_id,sb.lot_id)"""
        identifier, number, occurred, line = "sh.id", "sh.number", "pb.posted_at", "pl.id"
        qty, gross, net, status = (
            "pl.base_qty",
            "pl.gross_amount",
            "pl.net_amount",
            "'POSTED'::text",
        )
        lot, mfg, expiry, note = (
            "lot.lot_no",
            "lot.mfg_date",
            "lot.raw_expiry",
            "coalesce(sh.shipping_ref,'')",
        )
        condition = "sh.state='POSTED'"
    # Snapshots are authoritative. Unapproved orders explicitly use current draft names.
    return f"""SELECT {line} AS id,{identifier} AS document_id,{number} AS number,
      {occurred} AS occurred_at,o.id AS order_id,o.number AS order_number,o.revision_no,
      o.{party_key}_id AS party_id,o.warehouse_id,ol.sku_id,ol.line_no,
      coalesce(nullif(a.value#>>'{{order,{party_key}_name}}',''),
        CASE WHEN {draft} THEN p.name ELSE '历史未留存' END) AS party_name,
      coalesce(nullif(a.value#>>'{{order,warehouse_name}}',''),
        CASE WHEN {draft} THEN w.name ELSE '历史未留存' END) AS warehouse_name,
      coalesce(nullif(ol.document_snapshot#>>'{{values,product}}',''),'历史未留存') AS product_name,
      coalesce(ol.document_snapshot#>>'{{values,code}}','') AS product_code,
      coalesce(ol.document_snapshot#>>'{{values,specification}}','') AS specification,
      coalesce(ol.document_snapshot#>>'{{values,manufacturer_name}}','') AS manufacturer_name,
      coalesce(ol.document_snapshot#>>'{{values,approval_number}}','') AS approval_number,
      coalesce(nullif(ol.document_snapshot#>>'{{values,base_unit}}',''),'历史未留存') AS base_unit,
      (nullif(a.value#>>'{{order,{party_key}_name}}','') IS NOT NULL AND
       nullif(ol.document_snapshot#>>'{{values,product}}','') IS NOT NULL AND
       nullif(ol.document_snapshot#>>'{{values,base_unit}}','') IS NOT NULL) AS history_complete,
      {lot} AS lot_no,{mfg} AS mfg_date,{expiry} AS expiry_date,{note} AS note,
      {status} AS state,({qty}) AS qty,{qualified} AS qualified_qty,{rejected} AS rejected_qty,
      ol.tax_rate,({gross}) AS gross_amount,({net}) AS net_amount,
      round(({gross})-({net}),2) AS tax_amount
      FROM med.{prefix}_order o JOIN med.{prefix}_order_line ol
        ON (ol.tenant_id,ol.order_id)=(o.tenant_id,o.id)
      JOIN med.party p ON (p.tenant_id,p.id)=(o.tenant_id,o.{party_key}_id)
      JOIN med.warehouse w ON (w.tenant_id,w.id)=(o.tenant_id,o.warehouse_id)
      {joins}
      LEFT JOIN LATERAL (SELECT ae.reason::jsonb->'agreement' AS value FROM med.audit_event ae
        WHERE ae.tenant_id=o.tenant_id AND ae.resource_type='{prefix}_order' AND ae.resource_id=o.id
          AND ae.action='{prefix}.approved'
          AND coalesce(ae.reason::jsonb->>'revision_no',
            ae.reason::jsonb#>>'{{agreement,order,revision_no}}')=o.revision_no::text
        ORDER BY ae.created_at DESC,ae.id DESC LIMIT 1) a ON TRUE
      WHERE o.entity_id=:e AND {condition}"""


STATES = {
    "DRAFT": "待审核",
    "OPEN": "待收齐",
    "RECEIVED": "已收货",
    "CLOSED": "余量已关闭",
    "CANCELLED": "已取消",
    "APPROVED": "待发齐",
    "FULFILLED": "已发齐",
    "POSTED": "已过账",
    "QUALIFIED": "全部合格",
    "REJECTED": "全部拒收",
    "MIXED": "部分拒收",
}


def states_for(kind: Kind) -> dict[str, str]:
    keys = {
        "purchase": ["DRAFT", "OPEN", "RECEIVED", "CLOSED", "CANCELLED"],
        "receipt": ["RECEIVED"],
        "inspection": ["QUALIFIED", "MIXED", "REJECTED", "HAS_REJECTED"],
        "inbound": ["POSTED"],
        "sale": ["DRAFT", "APPROVED", "FULFILLED", "CLOSED", "CANCELLED"],
        "outbound": ["POSTED"],
    }[kind]
    result = {key: STATES.get(key, "含拒收") for key in keys}
    if kind == "purchase":
        result["RECEIVED"] = "已收齐"
    return result


def query_sql(kind: Kind, query: ReportQuery) -> tuple[str, dict[str, Any]]:
    filters, params = document_filters(
        "v",
        "concat_ws(' ',v.number,v.order_number,v.party_name,v.product_name,"
        "v.product_code,v.lot_no,v.note)",
        {
            s: "v.rejected_qty>0" if s == "HAS_REJECTED" else f"v.state='{s}'"
            for s in states_for(kind)
        },
        query.search,
        query.state,
        query.date_from,
        query.date_to,
        date_column="occurred_at",
    )
    for key, expression in (
        ("party", "v.party_name"),
        ("product", "concat_ws(' ',v.product_name,v.product_code,v.specification)"),
        ("warehouse", "v.warehouse_name"),
        ("lot_no", "v.lot_no"),
    ):
        if value := getattr(query, key):
            filters += f" AND strpos(lower({expression}),lower(:{key}))>0"
            params[key] = value
    for key in ("document_id", "order_id"):
        if value := getattr(query, key):
            filters += f" AND v.{key}=:{key}"
            params[key] = value
    return (
        f"WITH records AS ({source_sql(kind)}), "
        f"filtered AS (SELECT * FROM records v WHERE TRUE {filters}) ",
        {"e": query.entity_id, **params},
    )


SUMMARY = """SELECT document_id AS
  id,document_id,number,occurred_at,order_id,order_number,revision_no,
 party_id,party_name,warehouse_id,warehouse_name,count(*) AS line_count,count(DISTINCT sku_id)
   AS product_count,
 count(*) FILTER(WHERE qualified_qty>0) AS qualified_lines,count(*) FILTER(WHERE rejected_qty>0)
   AS rejected_lines,
 sum(gross_amount) AS gross_amount,sum(net_amount) AS net_amount,sum(tax_amount) AS tax_amount,
 CASE WHEN count(DISTINCT state)=1 THEN min(state) ELSE 'MIXED' END AS
   state,bool_and(history_complete) AS history_complete
 FROM filtered GROUP BY document_id,number,occurred_at,order_id,order_number,revision_no,
 party_id,party_name,warehouse_id,warehouse_name"""
MONEY = {"gross_amount", "net_amount", "tax_amount", "tax_rate"}


def fields(kind: Kind, view: str, money: bool) -> list[dict[str, Any]]:
    result = [
        ("number", "单据号", 245),
        ("occurred_at", "发生时间", 175),
        ("party_name", "往来单位", 200),
        ("warehouse_name", "仓库", 160),
        ("state", "状态／结果", 110),
    ]
    if view == "summary":
        result += [("line_count", "明细条数", 110), ("product_count", "商品种数", 110)]
        if kind == "inspection":
            result += [
                ("qualified_lines", "有合格品条数", 130),
                ("rejected_lines", "有拒收品条数", 130),
            ]
    else:
        result += [
            ("line_no", "原单行号", 100),
            ("product_code", "商品编码", 150),
            ("product_name", "商品名称", 220),
            ("specification", "规格型号", 150),
            ("base_unit", "基本单位", 90),
            ("qty", "本次数量", 120),
        ]
        if kind not in ("purchase", "sale"):
            result += [
                ("lot_no", "批号", 140),
                ("mfg_date", "生产日期", 120),
                ("expiry_date", "有效期至", 120),
            ]
        if kind == "inspection":
            result += [
                ("qualified_qty", "本次合格数量", 140),
                ("rejected_qty", "本次拒收数量", 140),
            ]
        result += [
            ("manufacturer_name", "生产企业", 200),
            ("approval_number", "批准／注册／备案号", 210),
        ]
    if money:
        prefix = "参考" if kind in ("receipt", "inspection") else ""
        result += [
            ("gross_amount", prefix + "含税金额（元）", 155),
            ("net_amount", prefix + "未税金额（元）", 155),
            ("tax_amount", prefix + "税额（元）", 140),
        ]
        if view == "detail":
            result += [("tax_rate", "税率", 90)]
    result += [
        ("order_number", "来源订单号", 235),
        ("revision_no", "订单内容版本", 120),
        ("history_complete", "历史信息", 130),
    ]
    if view == "detail":
        result += [("note", "本次备注", 220)]
    hidden = {
        "revision_no",
        "history_complete",
        "order_number",
        "manufacturer_name",
        "approval_number",
        "mfg_date",
        "tax_rate",
    }
    return [
        {
            "key": key,
            "label": label,
            "width": width,
            "visible": key not in hidden,
            "required": key == "number",
        }
        for key, label, width in result
    ]


def encode(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder={Decimal: lambda n: format(n, "f")})


def redact(rows: list[dict[str, Any]], money: bool) -> list[dict[str, Any]]:
    return rows if money else [{k: v for k, v in row.items() if k not in MONEY} for row in rows]


class DocumentReports:
    def __init__(self, uow: TenantUnitOfWork) -> None:
        self.uow = uow

    def listing(self, ctx: TenantContext, kind: Kind, query: ReportQuery) -> dict[str, Any]:
        money = permission(ctx, kind, query.entity_id)
        sql, params = query_sql(kind, query)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            require_entity(repo, query.entity_id)
            projection = SUMMARY if query.view == "summary" else "SELECT * FROM filtered"
            rows = repo.rows(
                sql
                + "SELECT * FROM ("
                + projection
                + ") result ORDER BY occurred_at DESC,document_id DESC,id LIMIT 50 OFFSET :offset",
                {**params, "offset": query.offset},
            )
            totals = repo.rows(
                sql
                + """SELECT count(*) AS lines,count(DISTINCT document_id) AS documents,
              count(DISTINCT sku_id) AS products,count(*) FILTER(WHERE NOT history_complete) AS
                incomplete,
              coalesce(sum(gross_amount),0) AS gross_amount,coalesce(sum(net_amount),0) AS
                net_amount,
              coalesce(sum(tax_amount),0) AS tax_amount FROM filtered""",
                params,
            )[0]
            units = repo.rows(
                sql
                + """SELECT base_unit,sum(qty) AS qty,sum(qualified_qty) AS qualified_qty,
              sum(rejected_qty) AS rejected_qty FROM filtered GROUP BY base_unit ORDER BY
                base_unit""",
                params,
            )
            chart = self.chart(repo, sql, params, query, money)
            total = totals["documents" if query.view == "summary" else "lines"]
            return dict(
                encode(
                    {
                        "items": redact(rows, money),
                        "total": total,
                        "has_more": query.offset + len(rows) < total,
                        "totals": redact([totals], money)[0],
                        "units": units,
                        "chart": chart,
                        "money_visible": money,
                        "fields": fields(kind, query.view, money),
                        "generated_at": repo.rows("SELECT transaction_timestamp() AS at", {})[0][
                            "at"
                        ],
                        "amount_basis": "按订单单价折算的参考金额，不代表入库应付"
                        if kind in ("receipt", "inspection")
                        else "已过账明细实际金额"
                        if kind in ("inbound", "outbound")
                        else "订单当前内容版本金额",
                        "number_basis": "YS／RK 为稳定查询编号；关联记录可查来源订单与收货单"
                        if kind in ("inspection", "inbound")
                        else "业务单据编号",
                        "states": states_for(kind),
                        "export_limit": EXPORT_LIMIT,
                    }
                )
            )

    @staticmethod
    def chart(
        repo: SqlSession, sql: str, params: dict[str, Any], query: ReportQuery, money: bool
    ) -> dict[str, Any]:
        metric = query.metric if money or query.metric not in MONEY else "documents"
        if metric == "qty" and not query.unit:
            return {
                "items": [],
                "metric": metric,
                "note": "选择一个基本单位后统计数量",
                "truncated": False,
            }
        grouping = {
            "day": ("to_char(occurred_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')", "''::text"),
            "month": ("to_char(occurred_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')", "''::text"),
            "party": ("party_name", "party_id::text"),
            "warehouse": ("warehouse_name", "warehouse_id::text"),
            "product": ("product_name", "sku_id::text"),
            "state": ("state", "''::text"),
        }
        label, identity = grouping[query.dimension]
        measure = {
            "documents": "count(DISTINCT document_id)",
            "lines": "count(*)",
            "qty": "sum(qty)",
            "gross_amount": "sum(gross_amount)",
            "net_amount": "sum(net_amount)",
            "tax_amount": "sum(tax_amount)",
        }[metric]
        unit_filter = " WHERE base_unit=:unit" if metric == "qty" else ""
        # Rank categories, then group the original facts again. Distinct document
        # counts in 'other' must not be derived by adding per-product counts.
        common = (
            sql
            + f""", chart_source AS (SELECT *,{label} AS label,{identity} AS identity
            FROM filtered {unit_filter}),
          groups AS (SELECT label,identity,{measure} AS value
            FROM chart_source GROUP BY label,identity),
          ranked AS (SELECT *,row_number() OVER(ORDER BY value DESC,label,identity) AS rank
            FROM groups) """
        )
        temporal = query.dimension in ("day", "month")
        if temporal:
            rows = repo.rows(
                common + "SELECT label,identity,value FROM ranked ORDER BY label LIMIT 367",
                {**params, "unit": query.unit},
            )
            if len(rows) > 366:
                return {
                    "items": [],
                    "metric": metric,
                    "note": "时间分组超过366项，请按月汇总或缩小日期范围",
                    "truncated": False,
                }
        else:
            rows = repo.rows(
                common
                + f""", categorized AS (
              SELECT c.*,CASE WHEN r.rank<=19 THEN c.label ELSE '其他' END AS bucket,
                CASE WHEN r.rank<=19 THEN c.identity ELSE '__other__' END AS bucket_id
              FROM chart_source c JOIN ranked r ON (c.label,c.identity)=(r.label,r.identity))
              SELECT bucket AS label,bucket_id AS identity,{measure} AS value FROM categorized
              GROUP BY bucket,bucket_id ORDER BY value DESC,bucket,bucket_id""",
                {**params, "unit": query.unit},
            )
        return {
            "items": rows,
            "metric": metric,
            "unit": query.unit if metric == "qty" else "元" if metric in MONEY else "",
            "note": (
                "全部筛选结果；分类超过19项合并为其他。"
                "商品分组的单据数允许同一单据出现在多个商品中。"
            )
            if not temporal
            else "全部筛选结果，按北京时间分组；未发生日期不补虚构记录。",
            "truncated": False,
        }

    def export(
        self, ctx: TenantContext, kind: Kind, query: ReportQuery, columns: str = ""
    ) -> tuple[str, str, int]:
        money = permission(ctx, kind, query.entity_id)
        registry = fields(kind, query.view, money)
        allowed = {f["key"]: f["label"] for f in registry}
        keys = columns.split(",") if columns else list(allowed)
        if len(set(keys)) != len(keys) or not keys or any(k not in allowed for k in keys):
            raise BusinessError("INVALID_REPORT_COLUMNS", "导出字段无效或无权读取", 422)
        sql, params = query_sql(kind, query)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            require_entity(repo, query.entity_id)
            projection = SUMMARY if query.view == "summary" else "SELECT * FROM filtered"
            rows = repo.rows(
                sql
                + "SELECT * FROM ("
                + projection
                + ") result ORDER BY occurred_at DESC,document_id DESC,id LIMIT :limit",
                {**params, "limit": EXPORT_LIMIT + 1},
            )
            if len(rows) > EXPORT_LIMIT:
                raise BusinessError(
                    "EXPORT_TOO_LARGE", "筛选结果超过10000行，请缩小范围后导出", 422
                )
        output = io.StringIO(newline="")
        writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerow([allowed[k] for k in keys])
        for row in rows:
            row["occurred_at"] = row["occurred_at"].astimezone(ZONE).isoformat(timespec="seconds")
            row["state"] = STATES.get(row["state"], row["state"])
            row["history_complete"] = (
                "已留存" if row["history_complete"] else "部分历史未留存／草稿"
            )
            writer.writerow([csv_cell(row.get(k)) for k in keys])
        return (
            "\ufeff" + output.getvalue(),
            f"{KINDS[kind]}-{'汇总' if query.view == 'summary' else '明细'}-"
            f"{datetime.now(ZONE):%Y%m%d-%H%M%S}.csv",
            len(rows),
        )

    def related(
        self, ctx: TenantContext, kind: Kind, entity: UUID, document: UUID
    ) -> dict[str, Any]:
        money = permission(ctx, kind, entity)
        query = ReportQuery(entity_id=entity, document_id=document)
        sql, params = query_sql(kind, query)
        with self.uow.open(ctx, readonly=True, repeatable=True) as session:
            repo = SqlSession(session)
            require_entity(repo, entity)
            orders = repo.rows(sql + "SELECT DISTINCT order_id FROM filtered", params)
            if not orders:
                raise BusinessError("REPORT_RECORD_NOT_FOUND", "该主体下未找到单据记录", 404)
            kinds: tuple[Kind, ...] = (
                ("sale", "outbound")
                if kind in ("sale", "outbound")
                else ("purchase", "receipt", "inspection", "inbound")
                if money
                else ("inspection",)
            )
            result = []
            for target in kinds:
                query = ReportQuery(entity_id=entity, order_id=orders[0]["order_id"])
                sql, params = query_sql(target, query)
                rows = repo.rows(
                    sql + "SELECT * FROM (" + SUMMARY + ") result ORDER BY occurred_at,id LIMIT 51",
                    params,
                )
                result.append(
                    {
                        "kind": target,
                        "name": KINDS[target],
                        "items": redact(rows[:50], money),
                        "has_more": len(rows) > 50,
                        "order_id": orders[0]["order_id"],
                    }
                )
            return dict(encode({"groups": result}))
