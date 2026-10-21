import csv
import io
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from test_api import api as api
from test_procurement import flow as flow
from test_quality_records import corpus
from test_sales import sale as sale

from medinv.modules.workspace.document_reports import DocumentReports, ReportQuery
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def q(f, **kwargs):
    return ReportQuery(entity_id=f.world.entity, **kwargs)


def test_stage_grains_no_process_rows_and_posted_money(flow):
    f = flow
    svc = DocumentReports(f.world.uow)
    receipt = f.receive()
    first = f.inspect(receipt["id"], "3", "1")
    f.post(first)
    f.inspect(receipt["id"], "2", "0")
    expected = {
        "purchase": (1, 1, 10, 130),
        "receipt": (1, 1, 10, 130),
        "inspection": (2, 2, 6, 78),
        "inbound": (1, 1, 3, 39),
    }
    for kind, (documents, lines, qty, amount) in expected.items():
        summary = svc.listing(f.maker, kind, q(f))
        detail = svc.listing(f.maker, kind, q(f, view="detail"))
        assert summary["totals"]["documents"] == documents
        assert summary["totals"]["lines"] == lines
        assert Decimal(summary["units"][0]["qty"]) == qty
        assert Decimal(summary["totals"]["gross_amount"]) == amount
        assert Decimal(summary["totals"]["gross_amount"]) == sum(
            Decimal(r["gross_amount"]) for r in detail["items"]
        )
        assert sum(Decimal(r["value"]) for r in summary["chart"]["items"]) == amount
        assert "posted_qty" not in detail["items"][0]
        assert summary["totals"]["incomplete"] == 0
        row = summary["items"][0]
        drilled = svc.listing(f.maker, kind, q(f, view="detail", document_id=row["document_id"]))
        assert drilled["total"] == row["line_count"]
        assert all(r["document_id"] == row["document_id"] for r in drilled["items"])
    related = svc.related(f.maker, "inspection", f.world.entity, UUID(first["inspection_id"]))
    assert [len(group["items"]) for group in related["groups"]] == [1, 1, 2, 1]


def test_sales_posted_records_exclude_pending_shipments_and_keep_exact_money(sale):
    s, f = sale, sale.f
    svc = DocumentReports(f.world.uow)
    assert svc.listing(f.maker, "sale", q(f))["totals"]["incomplete"] == 0
    sid = s.ready("3")
    assert svc.listing(f.maker, "outbound", q(f))["total"] == 0
    s.post(sid)
    detail = svc.listing(f.maker, "outbound", q(f, view="detail"))
    assert detail["total"] == 1 and Decimal(detail["totals"]["gross_amount"]) == 60
    assert detail["items"][0]["lot_no"] == "B001"
    assert Decimal(detail["totals"]["net_amount"]) == f.world.scalar(
        "SELECT sum(net_amount) FROM med.receivable_obligation"
    )
    assert detail["totals"]["incomplete"] == 0


def test_history_is_stable_and_scope_permissions_are_enforced(flow):
    f = flow
    svc = DocumentReports(f.world.uow)
    ins = f.inspect(f.receive()["id"], "2", "1")
    before = svc.listing(f.maker, "inspection", q(f, view="detail"))["items"][0]
    f.world.execute("UPDATE med.party SET name='今天的新名' WHERE id=:id", id=f.party["id"])
    f.world.execute(
        "UPDATE med.warehouse SET name='今天的新仓库' WHERE id=:id", id=f.world.warehouse
    )
    after = svc.listing(f.maker, "inspection", q(f, view="detail"))["items"][0]
    assert after == before
    quality = replace(f.maker, permissions=frozenset({"quality.read"}))
    result = svc.listing(quality, "inspection", q(f))
    assert not result["money_visible"] and "gross_amount" not in result["items"][0]
    assert "gross_amount" not in result["totals"] and result["chart"]["metric"] == "documents"
    assert [
        g["kind"]
        for g in svc.related(quality, "inspection", f.world.entity, UUID(ins["inspection_id"]))[
            "groups"
        ]
    ] == ["inspection"]
    with pytest.raises(BusinessError):
        svc.export(quality, "inspection", q(f), "gross_amount")
    for ctx in (
        replace(f.maker, permissions=frozenset()),
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, tenant_id=uuid4()),
    ):
        for method in (svc.listing, svc.export):
            with pytest.raises(BusinessError):
                method(ctx, "inspection", q(f))
    with pytest.raises(BusinessError):
        svc.related(f.maker, "inspection", f.world.entity, uuid4())


def test_full_filtered_totals_charts_pagination_export_and_literal_filters(flow):
    from datetime import datetime

    from medinv.modules.workspace.quality_records import ZONE

    f = flow
    corpus(f, [datetime.now(ZONE)] * 61)
    svc = DocumentReports(f.world.uow)
    report = svc.listing(f.maker, "inspection", q(f, view="detail", offset=50, metric="lines"))
    assert len(report["items"]) == 11 and report["total"] == 61
    assert sum(Decimal(r["value"]) for r in report["chart"]["items"]) == 61
    assert svc.listing(f.maker, "inspection", q(f, product="P-NEW"))["total"] == 61
    for search in ("%", "' OR TRUE--", "missing"):
        assert svc.listing(f.maker, "inspection", q(f, search=search))["total"] == 0
    text, _, count = svc.export(
        f.maker, "inspection", q(f, view="detail", offset=50), "number,qty,gross_amount"
    )
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    assert count == 61 and len(rows) == 62 and len(rows[0]) == 3
    for dimension in ("day", "month", "party", "warehouse", "product", "state"):
        report = svc.listing(
            f.maker, "inspection", q(f, dimension=dimension, metric="qty", unit="个")
        )
        assert sum(Decimal(r["value"]) for r in report["chart"]["items"]) == 61


def test_http_report_and_export_validation(api, flow):
    client, _ = api
    params = {"entity_id": str(flow.world.entity)}
    response = client.get("/api/v1/document-reports/purchase", params=params)
    assert response.status_code == 403
    assert client.get("/api/v1/document-reports/not-a-kind", params=params).status_code == 422
    assert (
        client.get(
            "/api/v1/document-reports/purchase", params={**params, "date_to": "bad"}
        ).status_code
        == 422
    )


def test_chart_other_deduplicates_documents_and_quantity_never_mixes_units(flow):
    from medinv.platform.database.sql import SqlSession

    f = flow
    # 21 product groups belong to the same document; 'other' must remain one
    # distinct document instead of summing the last two product-group counts.
    sql = """WITH filtered AS (SELECT 'same-doc' AS document_id,
      '商品'||lpad(n::text,2,'0') AS product_name,n::text AS sku_id,
      n::numeric AS qty,n::numeric AS gross_amount,
      CASE WHEN n%2=1 THEN '个' ELSE '箱' END AS base_unit
      FROM generate_series(1,21) n) """
    with f.world.uow.open(f.maker, readonly=True) as session:
        repo = SqlSession(session)
        chart = DocumentReports.chart(
            repo, sql, {}, q(f, dimension="product", metric="documents"), True
        )
        assert len(chart["items"]) == 20
        assert next(row for row in chart["items"] if row["label"] == "其他")["value"] == 1
        chart = DocumentReports.chart(
            repo, sql, {}, q(f, dimension="product", metric="gross_amount"), True
        )
        assert sum(row["value"] for row in chart["items"]) == 231
        chart = DocumentReports.chart(
            repo, sql, {}, q(f, dimension="product", metric="qty", unit="个"), True
        )
        assert sum(row["value"] for row in chart["items"]) == 121
        chart = DocumentReports.chart(repo, sql, {}, q(f, dimension="product", metric="qty"), True)
        assert chart["items"] == [] and "单位" in chart["note"]


def test_dates_read_only_export_limit_and_invalid_columns(flow, monkeypatch):
    from datetime import datetime, timedelta

    from medinv.modules.workspace import document_reports
    from medinv.modules.workspace.quality_records import ZONE

    f = flow
    day = f.day + timedelta(days=1)
    midnight = datetime.combine(day, datetime.min.time(), ZONE)
    corpus(
        f,
        [
            midnight - timedelta(microseconds=1),
            midnight,
            midnight + timedelta(days=1) - timedelta(microseconds=1),
            midnight + timedelta(days=1),
        ],
    )
    svc = DocumentReports(f.world.uow)
    before = {
        table: f.world.scalar(f"SELECT count(*) FROM med.{table}")
        for table in ("audit_event", "posting_batch", "stock_ledger", "inspection")
    }
    result = svc.listing(
        f.maker, "inspection", q(f, date_from=day, date_to=day, metric="documents")
    )
    assert result["total"] == 2
    assert result["chart"]["items"][0]["label"] == str(day)
    svc.export(f.maker, "inspection", q(f))
    assert before == {
        table: f.world.scalar(f"SELECT count(*) FROM med.{table}") for table in before
    }
    for columns in ("number,number", "not_allowed", "number,created_by"):
        with pytest.raises(BusinessError):
            svc.export(f.maker, "inspection", q(f), columns)
    monkeypatch.setattr(document_reports, "EXPORT_LIMIT", 3)
    with pytest.raises(BusinessError) as exc:
        svc.export(f.maker, "inspection", q(f))
    assert exc.value.code == "EXPORT_TOO_LARGE"


def test_http_filters_flat_model_csv_and_related_scope(api, flow):
    from sqlalchemy import text

    client, control = api
    f = flow
    ins = f.inspect(f.receive()["id"], "2", "1")
    with control.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=ARRAY['purchase.read'] "
                "WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    params = {
        "entity_id": str(f.world.entity),
        "view": "detail",
        "document_id": str(ins["inspection_id"]),
    }
    response = client.get("/api/v1/document-reports/inspection", params=params)
    assert response.status_code == 200 and response.json()["data"]["total"] == 1
    exported = client.get(
        "/api/v1/document-reports/inspection/export", params={**params, "columns": "number,qty"}
    )
    assert exported.status_code == 200 and exported.headers["X-Export-Rows"] == "1"
    assert len(next(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))) == 2
    assert (
        client.get(
            f"/api/v1/document-reports/inspection/{ins['inspection_id']}/related",
            params={"entity_id": str(f.world.entity)},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/document-reports/inspection/{ins['inspection_id']}/related",
            params={"entity_id": str(uuid4())},
        ).status_code
        == 404
    )
