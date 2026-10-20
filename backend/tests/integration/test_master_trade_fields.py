from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_procurement import flow as flow
from test_product_master import body
from test_product_master import master as master

from medinv.modules.catalog.api.codes import CatalogCodeCreate
from medinv.modules.catalog.api.master import BasicUnitBarcode, MasterSave
from medinv.modules.catalog.api.prices import ReferencePricesUpdate
from medinv.modules.catalog.application.codes import CatalogCodes
from medinv.modules.catalog.application.master import ProductMaster
from medinv.modules.catalog.application.prices import ReferencePrices
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def price(version="0", value="1.123456"):
    return ReferencePricesUpdate(
        expected_version=version,
        purchase_price="0",
        wholesale_price=value,
        retail_price=None,
        note="基本单位参考价",
        reason="录入价格",
    )


def change(detail, value=None, prices=None):
    base = [r for r in detail["values"]["barcodes"] if r["packaging_id"] is None]
    return MasterSave(
        product_id=UUID(detail["id"]),
        category_node_id=UUID(detail["category_node_id"]),
        form_revision=detail["form_revision"],
        expected_version=detail["version"],
        values={},
        basic_unit_barcode=BasicUnitBarcode(
            value=value,
            expected_entries=[{"id": r["id"], "version": str(r["version"])} for r in base],
        ),
        reference_prices=prices,
    )


def test_new_inline_trade_fields_share_transaction_history_and_idempotency(master):
    service, _, ctx, world = master
    req = body().model_copy(
        update={
            "basic_unit_barcode": BasicUnitBarcode(value="0000123-ABC"),
            "reference_prices": price(),
        }
    )
    key = str(uuid4())
    created = service.save(ctx, req, key)
    assert service.save(ctx, req, key) == created
    detail = service.detail(ctx, UUID(created["id"]))
    assert detail["values"]["barcodes"][0]["external_code"] == "0000123-ABC"
    assert detail["values"]["barcodes"][0]["packaging_id"] is None
    assert detail["values"]["reference_prices"][0]["purchase_price"] == "0.000000"
    assert detail["values"]["reference_prices"][0]["wholesale_price"] == "1.123456"
    assert detail["values"]["reference_prices"][0]["retail_price"] is None
    snapshot = service.history(ctx, UUID(created["id"]), 1)["snapshot"]
    assert snapshot["barcodes"] == detail["values"]["barcodes"]
    assert snapshot["reference_prices"] == detail["values"]["reference_prices"]
    assert world.scalar("SELECT count(*) FROM med.catalog_code") == 1


def test_duplicate_barcode_rolls_back_new_product_and_replacement(master):
    service, _, ctx, world = master
    first = service.save(
        ctx,
        body().model_copy(update={"basic_unit_barcode": BasicUnitBarcode(value="taken")}),
        str(uuid4()),
    )
    second = service.save(
        ctx,
        body().model_copy(update={"basic_unit_barcode": BasicUnitBarcode(value="retained")}),
        str(uuid4()),
    )
    before = service.detail(ctx, UUID(second["id"]))
    with pytest.raises(BusinessError, match="条码已有"):
        service.save(ctx, change(before, "taken", price()), str(uuid4()))
    assert service.detail(ctx, UUID(second["id"])) == before
    total = world.scalar("SELECT count(*) FROM med.product")
    with pytest.raises(BusinessError):
        service.save(
            ctx,
            body().model_copy(update={"basic_unit_barcode": BasicUnitBarcode(value="taken")}),
            str(uuid4()),
        )
    assert world.scalar("SELECT count(*) FROM med.product") == total
    assert first["id"] != second["id"]


def test_existing_extra_codes_preserved_and_old_api_changes_cause_conflict(master):
    service, _, ctx, world = master
    result = service.save(ctx, body(), str(uuid4()))
    detail = service.detail(ctx, UUID(result["id"]))
    sku = UUID(detail["skus"][0]["id"])
    codes = CatalogCodes(world.uow)
    for value in ["older", "current"]:
        codes.create(
            ctx,
            "skus",
            sku,
            CatalogCodeCreate(
                scheme="BARCODE", external_code=value, evidence_ref="标签录入", reason="测试"
            ),
            str(uuid4()),
        )
    detail = service.detail(ctx, UUID(result["id"]))
    service.save(ctx, change(detail, "new"), str(uuid4()))
    latest = service.detail(ctx, UUID(result["id"]))
    assert [c["external_code"] for c in latest["values"]["barcodes"]] == ["new", "older"]
    assert (
        world.scalar("SELECT state FROM med.catalog_code WHERE external_code='current'")
        == "INACTIVE"
    )
    stale = change(latest, "attempt")
    codes.create(
        ctx,
        "skus",
        sku,
        CatalogCodeCreate(
            scheme="BARCODE", external_code="other-window", evidence_ref="标签录入", reason="测试"
        ),
        str(uuid4()),
    )
    with pytest.raises(BusinessError, match="条码已变化"):
        service.save(ctx, stale, str(uuid4()))
    latest = service.detail(ctx, UUID(result["id"]))
    service.save(ctx, change(latest, None), str(uuid4()))
    assert [
        c["external_code"] for c in service.detail(ctx, UUID(result["id"]))["values"]["barcodes"]
    ] == ["new", "older"]


def test_stale_prices_roll_back_barcode_and_product(master):
    service, _, ctx, world = master
    result = service.save(ctx, body(), str(uuid4()))
    detail = service.detail(ctx, UUID(result["id"]))
    sku = UUID(detail["skus"][0]["id"])
    ReferencePrices(world.uow).update(ctx, sku, price(), str(uuid4()))
    before = service.detail(ctx, UUID(result["id"]))
    with pytest.raises(BusinessError, match="参考价格已变化"):
        service.save(ctx, change(detail, "must-rollback", price()), str(uuid4()))
    assert service.detail(ctx, UUID(result["id"])) == before
    assert world.scalar("SELECT count(*) FROM med.catalog_code") == 0


def test_inapplicable_permissions_and_tenant_cannot_write_trade_fields(master):
    service, _, ctx, world = master
    before = world.scalar("SELECT count(*) FROM med.product")
    req = body("service").model_copy(
        update={"basic_unit_barcode": BasicUnitBarcode(value="not-service")}
    )
    with pytest.raises(BusinessError, match="不适用"):
        service.save(ctx, req, str(uuid4()))
    assert world.scalar("SELECT count(*) FROM med.product") == before
    created = service.save(ctx, body(), str(uuid4()))
    detail = service.detail(ctx, UUID(created["id"]))
    for denied in [
        replace(ctx, tenant_id=uuid4()),
        replace(ctx, permissions=frozenset({"catalog.read"})),
    ]:
        with pytest.raises(BusinessError):
            service.save(denied, change(detail, "denied", price()), str(uuid4()))
    for value in ["a b", "\n", "x" * 129]:
        with pytest.raises(ValidationError):
            BasicUnitBarcode(value=value)


def test_inline_trade_only_does_not_revoke_approved_product(flow):
    f = flow
    service = ProductMaster(f.world.uow)
    detail = service.detail(f.maker, UUID(f.product["id"]))
    assert detail["approval_state"] == "APPROVED"
    service.save(f.maker, change(detail, "approved-unit", price()), str(uuid4()))
    assert service.detail(f.maker, UUID(f.product["id"]))["approval_state"] == "APPROVED"
