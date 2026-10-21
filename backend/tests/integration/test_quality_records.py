import csv
import io
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_api import api as api
from test_procurement import flow as flow

from medinv.modules.workspace import quality_records
from medinv.modules.workspace.quality_records import QualityRecordQuery, QualityRecords, query_sql
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def query(f, **filters):
    return QualityRecordQuery(entity_id=f.world.entity, **filters)


def prepared(f):
    receipt = f.receive()
    f.inspect(receipt["id"], "2", "0")
    f.inspect(receipt["id"], "1", "1")
    f.inspect(receipt["id"], "0", "2")
    return QualityRecords(f.world.uow)


def test_filters_totals_are_per_inspection_line_and_use_literal_search(flow):
    f = flow
    svc = prepared(f)
    report = svc.listing(f.maker, query(f))
    assert report["total"] == report["inspections"] == 3
    assert report["incomplete"] == 0
    assert report["totals"] == [
        {"base_unit": "个", "qualified_qty": "3.000000", "rejected_qty": "3.000000"}
    ]
    for result in ("QUALIFIED", "MIXED", "REJECTED"):
        rows = svc.listing(f.maker, query(f, result=result))["items"]
        assert len(rows) == 1 and rows[0]["result"] == result
    assert svc.listing(f.maker, query(f, result="HAS_REJECTED"))["total"] == 2
    for filters in (
        {"supplier": "测试供应商"},
        {"product": "P-NEW"},
        {"product": "单件"},
        {"lot_no": "B001"},
        {"warehouse_id": f.world.warehouse},
        {"search": "DELIVERY"},
    ):
        assert svc.listing(f.maker, query(f, **filters))["total"] == 3
    for filters in ({"search": "%"}, {"lot_no": "' OR true--"}, {"warehouse_id": uuid4()}):
        assert svc.listing(f.maker, query(f, **filters))["total"] == 0
    assert report["can_open_source"] and report["items"][0]["order_id"] == str(f.oid)


def test_snapshot_names_and_codes_survive_master_renaming(flow):
    f = flow
    svc = prepared(f)
    before = svc.listing(f.maker, query(f))["items"][0]
    f.world.execute("UPDATE med.product SET name='当前新商品名称' WHERE id=:id", id=f.product["id"])
    f.world.execute("UPDATE med.party SET name='当前新单位名称' WHERE id=:id", id=f.party["id"])
    f.world.execute(
        "UPDATE med.warehouse SET name='当前新仓库名称' WHERE id=:id", id=f.world.warehouse
    )
    after = svc.listing(f.maker, query(f))["items"][0]
    for key in ("product_name", "product_code", "party_name", "warehouse_name", "base_unit"):
        assert after[key] == before[key]
    assert svc.listing(f.maker, query(f, search="当前新"))["total"] == 0


def test_permissions_tenant_scope_and_read_only_effects(flow):
    f = flow
    svc = prepared(f)
    reader = replace(f.maker, permissions=frozenset({"quality.read"}))
    assert not svc.listing(reader, query(f))["can_open_source"]
    before = {
        name: f.world.scalar(f"SELECT count(*) FROM med.{name}")
        for name in ("audit_event", "stock_ledger", "posting_batch", "inspection")
    }
    assert svc.export(reader, query(f))[2] == 3
    for ctx in (
        replace(reader, permissions=frozenset()),
        replace(reader, entity_ids=frozenset()),
        replace(reader, tenant_id=uuid4()),
    ):
        for method in (svc.listing, svc.export):
            with pytest.raises(BusinessError):
                method(ctx, query(f))
    assert before == {name: f.world.scalar(f"SELECT count(*) FROM med.{name}") for name in before}


def corpus(f, timestamps):
    # Isolated query corpus: explicit timestamps are inserted, never edited. The
    # business API's cumulative checks are covered by procurement integration tests.
    receipt = f.receive()
    line = f.detail()["receipts"][0]["lines"][0]["id"]
    with f.world.uow.open(f.maker) as session:
        for timestamp in timestamps:
            iid = uuid4()
            params = {
                "t": f.maker.tenant_id,
                "id": iid,
                "r": receipt["id"],
                "a": f.reviewer.principal_id,
                "at": timestamp,
                "l": line,
                "lid": uuid4(),
            }
            session.execute(
                text("""INSERT INTO med.inspection
              (tenant_id,id,receipt_id,created_by,inspected_at,note)
              VALUES(:t,:id,:r,:a,:at,'query corpus')"""),
                params,
            )
            session.execute(
                text("""INSERT INTO med.inspection_line
              (tenant_id,id,inspection_id,receipt_line_id,qualified_qty,rejected_qty)
              VALUES(:t,:lid,:id,:l,1,0)"""),
                params,
            )
    return QualityRecords(f.world.uow)


def test_pagination_export_all_rows_and_refuse_truncation(flow, monkeypatch):
    f = flow
    svc = corpus(f, [datetime.now(quality_records.ZONE)] * 61)
    first, last = (svc.listing(f.maker, query(f, offset=i)) for i in (0, 50))
    assert first["total"] == last["total"] == 61 and len(first["items"]) == 50
    assert first["has_more"] and not last["has_more"] and len(last["items"]) == 11
    assert len({row["id"] for row in first["items"] + last["items"]}) == 61
    exported, filename, count = svc.export(f.maker, query(f, offset=50))
    assert count == 61 and filename.endswith(".csv") and exported.startswith("\ufeff")
    assert len(list(csv.reader(io.StringIO(exported.lstrip("\ufeff"))))) == 62
    monkeypatch.setattr(quality_records, "EXPORT_LIMIT", 60)
    with pytest.raises(BusinessError) as exc:
        svc.export(f.maker, query(f))
    assert exc.value.code == "EXPORT_TOO_LARGE"


def test_beijing_inclusive_dates_and_invalid_ranges(flow):
    f = flow
    day = f.day + timedelta(days=1)
    start = datetime.combine(day, datetime.min.time(), quality_records.ZONE)
    svc = corpus(
        f,
        [
            start - timedelta(microseconds=1),
            start,
            start + timedelta(days=1) - timedelta(microseconds=1),
            start + timedelta(days=1),
        ],
    )
    result = svc.listing(f.maker, query(f, date_from=day, date_to=day))
    assert result["total"] == 2
    _, params = query_sql(query(f, date_from=day, date_to=day))
    assert params["document_from"].utcoffset() == timedelta(hours=8)
    for filters in (
        {"date_from": day, "date_to": day - timedelta(days=1)},
        {"date_to": date.max},
        {"offset": -1},
        {"result": "INVALID"},
    ):
        with pytest.raises(ValidationError):
            query(f, **filters)


def test_csv_quotes_formulas_multiline_and_decimal_precision(flow):
    f = flow
    receipt = f.receive()
    from medinv.modules.procurement.api.schemas import InspectionCreate

    doc = f.detail()
    f.service.inspect(
        f.reviewer,
        UUID(receipt["id"]),
        InspectionCreate(
            expected_version=doc["receipts"][0]["version"],
            note='=测试,"引号"\n下一行',
            lines=[
                {
                    "receipt_line_id": doc["receipts"][0]["lines"][0]["id"],
                    "qualified_qty": "2",
                    "rejected_qty": "1",
                }
            ],
        ),
        str(uuid4()),
    )
    svc = QualityRecords(f.world.uow)
    content, _, count = svc.export(f.maker, query(f))
    rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    assert count == len(rows) == 1
    assert rows[0]["验收意见"] == '\'=测试,"引号"\n下一行'
    assert rows[0]["合格数量"] == "'2.000000"
    assert "+08:00" in rows[0]["验收时间（北京时间）"]
    for value in ("=1+1", "+1", "-1", "@SUM(1)", "\t=1", "\n=1", " ＠x", "'text"):
        assert quality_records.csv_cell(value).startswith("'")
    assert Decimal(svc.listing(f.maker, query(f))["items"][0]["qualified_qty"]) == 2


def test_http_filters_csv_headers_and_permissions(api, flow):
    client, control = api
    f = flow
    prepared(f)
    params = {"entity_id": str(f.world.entity), "result": "MIXED"}
    assert client.get("/api/v1/quality-records", params=params).status_code == 403
    with control.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=ARRAY['quality.read'] "
                "WHERE tenant_id=:t AND principal_id=:p"
            ),
            {"t": f.maker.tenant_id, "p": f.maker.principal_id},
        )
    response = client.get("/api/v1/quality-records", params=params)
    assert response.status_code == 200 and response.json()["data"]["total"] == 1
    exported = client.get("/api/v1/quality-records/export", params=params)
    assert exported.status_code == 200 and exported.content.startswith(b"\xef\xbb\xbf")
    assert exported.headers["Content-Type"].startswith("text/csv")
    assert exported.headers["Cache-Control"] == "no-store"
    assert exported.headers["X-Export-Rows"] == "1"
    assert "filename*=UTF-8''" in exported.headers["Content-Disposition"]
    assert (
        client.get("/api/v1/quality-records", params={**params, "date_to": "bad"}).status_code
        == 422
    )
    assert (
        client.get("/api/v1/quality-records/export", params={**params, "result": "bad"}).status_code
        == 422
    )


def test_missing_approval_evidence_is_marked_without_using_current_names(flow):
    f = flow
    # A dated query corpus deliberately has no corresponding earlier approval
    # evidence. It models the read-side fallback for historical missing evidence.
    old = datetime.combine(f.day - timedelta(days=1), datetime.min.time(), quality_records.ZONE)
    svc = corpus(f, [old])
    report = svc.listing(f.maker, query(f))
    assert report["incomplete"] == 1
    row = report["items"][0]
    assert row["party_name"] == row["warehouse_name"] == "历史未留存"
    assert row["product_name"] == "采购测试商品"
    assert not row["history_complete"]
    exported = svc.export(f.maker, query(f))[0]
    assert "部分历史未留存" in exported and "测试供应商" not in exported


def test_one_inspection_multiple_lines_counts_once_and_groups_units(flow):
    from medinv.modules.catalog.api.schemas import ProductCreate, Review
    from medinv.modules.procurement.api.schemas import InspectionCreate, ReceiptCreate

    f = flow
    product = f.catalog.create_product(
        f.maker,
        ProductCreate(
            code="BOX",
            name="按盒计量测试商品",
            category="DEVICE",
            manufacturer_name="测试厂家",
            specification="测试规格",
            approval_number="TEST",
            valid_from=f.day,
            valid_until=f.day + timedelta(days=365),
            sku_code="BOX-SKU",
            base_unit="盒",
            qty_scale=0,
        ),
        str(uuid4()),
    )
    f.catalog.review(
        f.reviewer,
        "product",
        UUID(product["id"]),
        Review(expected_version="1", decision="APPROVE", evidence_ref="TEST", reason="测试"),
        str(uuid4()),
    )
    oid = f.order(
        lines=[
            {"sku_id": f.sku, "qty": "2", "unit_price": "10", "tax_rate": "0.13"},
            {"sku_id": product["sku_id"], "qty": "3", "unit_price": "20", "tax_rate": "0.13"},
        ]
    )
    doc = f.service.detail(f.maker, oid)
    receipt = f.service.receive(
        f.maker,
        oid,
        ReceiptCreate(
            expected_version=doc["order"]["version"],
            shipment_ref="TWO-UNITS",
            transport_note="测试",
            lines=[
                {
                    "order_line_id": line["id"],
                    "location_id": f.world.location,
                    "qty": line["qty"],
                    "lot_no": "UNITS",
                    "mfg_date": f.day,
                    "expiry_date": f.day + timedelta(days=300),
                }
                for line in doc["lines"]
            ],
        ),
        str(uuid4()),
    )
    doc = f.service.detail(f.maker, oid)
    f.service.inspect(
        f.reviewer,
        UUID(receipt["id"]),
        InspectionCreate(
            expected_version=doc["receipts"][0]["version"],
            note="两种基本单位",
            lines=[
                {
                    "receipt_line_id": line["id"],
                    "qualified_qty": line["qty"],
                    "rejected_qty": "0",
                }
                for line in doc["receipts"][0]["lines"]
            ],
        ),
        str(uuid4()),
    )
    report = QualityRecords(f.world.uow).listing(f.maker, query(f))
    assert report["inspections"] == 1 and report["total"] == 2
    assert {r["base_unit"]: Decimal(r["qualified_qty"]) for r in report["totals"]} == {
        "个": Decimal("2"),
        "盒": Decimal("3"),
    }
