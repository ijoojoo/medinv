from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_procurement import flow as flow

from medinv.modules.catalog.api.dictionaries import DictionaryUpdate
from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.procurement.api.schemas import Approval, InspectionCreate, ReceiptCreate
from medinv.modules.procurement.application.conclusions import options
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def update_dictionary(f, key, items, mode="STRICT"):
    service = CatalogDictionaries(f.world.uow)
    old = service.detail(f.maker, key)
    return service.update(
        f.maker,
        key,
        DictionaryUpdate(
            expected_version=old["version"], mode=mode, items=items, reason="维护办理用语"
        ),
        str(uuid4()),
    )


def test_receipt_inspection_posting_follow_source_order_even_with_reverse_submissions(flow):
    f = flow
    oid = f.order(lines=[{"sku_id": f.sku, "qty": str(i), "unit_price": "2"} for i in (3, 7, 11)])
    doc = f.service.detail(f.maker, oid)
    ids = [line["id"] for line in doc["lines"]]
    receipt = f.service.receive(
        f.maker,
        oid,
        ReceiptCreate(
            expected_version=doc["order"]["version"],
            shipment_ref="MULTI",
            transport_note="核验",
            lines=[
                dict(
                    order_line_id=line["id"],
                    location_id=f.world.location,
                    qty=line["qty"],
                    lot_no=f"line-{i}",
                    mfg_date=f.day,
                    expiry_date=f.day.replace(year=f.day.year + 1),
                )
                for i, line in reversed(list(enumerate(doc["lines"])))
            ],
        ),
        str(uuid4()),
    )
    rec = f.service.detail(f.maker, oid)["receipts"][0]
    assert [line["order_line_id"] for line in rec["lines"]] == ids
    receipt_ids = [line["id"] for line in rec["lines"]]
    result = f.service.inspect(
        f.reviewer,
        UUID(receipt["id"]),
        InspectionCreate(
            expected_version=rec["version"],
            conclusion="合格",
            lines=[
                dict(
                    receipt_line_id=line["id"], qualified_qty=line["pending_qty"], rejected_qty="0"
                )
                for line in reversed(rec["lines"])
            ],
        ),
        str(uuid4()),
    )
    rec = f.service.detail(f.maker, oid)["receipts"][0]
    assert [line["receipt_line_id"] for line in rec["inspections"]] == receipt_ids
    assert [line["receipt_line_id"] for line in rec["postings"][0]["lines"]] == receipt_ids
    assert {line["note"] for line in rec["inspections"]} == {"合格"}
    f.post(result)


def test_dictionary_defaults_scope_required_active_and_strict_mode(flow):
    f = flow
    reader = replace(f.maker, permissions=frozenset({"purchase.read"}))
    assert [d["items"][0]["value"] for d in options(f.world.uow, reader)["items"]] == [
        "同意",
        "合格",
        "部分合格",
        "不合格",
    ]
    with pytest.raises(BusinessError):
        options(f.world.uow, replace(reader, permissions=frozenset()))
    for mode, active in [("SUGGEST", True), ("STRICT", False)]:
        with pytest.raises(BusinessError, match="至少保留"):
            update_dictionary(
                f, "purchase_approval", [dict(value="同意", active=active, order=10)], mode
            )
    update_dictionary(
        f,
        "purchase_approval",
        [
            dict(value="同意", active=False, order=10),
            dict(value="核对无误，同意", active=True, order=0),
        ],
    )
    another = replace(reader, tenant_id=uuid4())
    assert options(f.world.uow, another)["items"][0]["version"] == "0"
    assert options(f.world.uow, reader)["items"][0]["version"] == "1"


def test_approval_dictionary_stale_values_rejected_and_exact_retry_survives_later_change(flow):
    f = flow
    oid = f.order(approve=False)
    body = Approval(expected_version="1", conclusion="同意")
    key = str(uuid4())
    result = f.service.transition(f.reviewer, oid, body, key, True)
    update_dictionary(
        f,
        "purchase_approval",
        [
            dict(value="同意", active=False, order=10),
            dict(value="核对无误，同意", active=True, order=0),
        ],
    )
    assert f.service.transition(f.reviewer, oid, body, key, True) == result
    other = f.order(approve=False)
    with pytest.raises(BusinessError, match="启用的字典"):
        f.service.transition(f.reviewer, other, body, str(uuid4()), True)
    assert f.service.detail(f.maker, other)["order"]["approval_state"] == "DRAFT"
    with pytest.raises(BusinessError, match="不能审核"):
        f.service.transition(
            f.maker,
            other,
            Approval(expected_version="1", conclusion="核对无误，同意"),
            str(uuid4()),
            True,
        )


@pytest.mark.parametrize(
    "q,r,conclusion,note,error",
    [
        ("8", "2", "合格", "破损", "启用的字典"),
        ("8", "2", "部分合格", "", "拒收原因"),
        ("0", "10", "部分合格", "破损", "启用的字典"),
        ("10", "1", "部分合格", "破损", "不超过待验"),
    ],
)
def test_inspection_decision_matches_quantities_and_rejection_needs_reason(
    flow, q, r, conclusion, note, error
):
    f = flow
    received = f.receive()
    rec = f.detail()["receipts"][0]
    with pytest.raises(BusinessError, match=error):
        f.service.inspect(
            f.reviewer,
            UUID(received["id"]),
            InspectionCreate(
                expected_version=rec["version"],
                conclusion=conclusion,
                note=note,
                lines=[
                    dict(receipt_line_id=rec["lines"][0]["id"], qualified_qty=q, rejected_qty=r)
                ],
            ),
            str(uuid4()),
        )
    latest = f.detail()["receipts"][0]
    assert latest["inspections"] == []
    assert latest["version"] == rec["version"]


def test_custom_inspection_wording_is_kept_in_history_with_evidence(flow):
    f = flow
    update_dictionary(
        f,
        "inspection_mixed",
        [
            dict(value="部分合格", active=True, order=10),
            dict(value="部分拒收，其余合格", active=True, order=0),
        ],
    )
    received = f.receive()
    rec = f.detail()["receipts"][0]
    f.service.inspect(
        f.reviewer,
        UUID(received["id"]),
        InspectionCreate(
            expected_version=rec["version"],
            conclusion="部分拒收，其余合格",
            note="外包装破损两件",
            lines=[
                dict(receipt_line_id=rec["lines"][0]["id"], qualified_qty="8", rejected_qty="2")
            ],
        ),
        str(uuid4()),
    )
    assert (
        f.detail()["receipts"][0]["inspections"][0]["note"] == "部分拒收，其余合格；外包装破损两件"
    )


def test_legacy_note_payloads_preserve_idempotency_and_blank_evidence_rejected():
    approval = {"expected_version": "1", "reason": "原有审批意见"}
    assert Approval(**approval).model_dump(mode="json") == approval
    inspection = dict(
        expected_version="1",
        note="原有验收意见",
        lines=[
            dict(receipt_line_id=str(uuid4()), qualified_qty="1.000000", rejected_qty="0.000000")
        ],
    )
    assert InspectionCreate(**inspection).model_dump(mode="json") == inspection
    with pytest.raises(ValidationError):
        Approval(expected_version="1", reason=" ")
    with pytest.raises(ValidationError):
        InspectionCreate(**{**inspection, "note": " "})
