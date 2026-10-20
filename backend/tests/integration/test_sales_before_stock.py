"""Wholesale demand can precede purchasing; stock operations cannot."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_procurement import flow as flow

from medinv.modules.catalog.api.schemas import ProductCreate, Review
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.retail.api.schemas import RetailCreate
from medinv.modules.retail.application.service import RetailService
from medinv.modules.sales.api.schemas import (
    CreditSet,
    ReplenishmentCreate,
    SalesCreate,
    SalesRevision,
    ShipmentCreate,
)
from medinv.modules.sales.application.amendments import revise
from medinv.modules.sales.application.fulfillment import allocate
from medinv.modules.sales.application.replenishment import ReplenishmentService
from medinv.modules.sales.application.service import SalesService
from medinv.modules.sales.application.stock import SaleStockQueries
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh(flow):
    f = flow
    product = f.catalog.create_product(
        f.maker,
        ProductCreate(
            code="FIRST-SALE",
            name="先销售后采购的普通器械",
            category="DEVICE",
            manufacturer_name="测试厂家",
            specification="单支",
            approval_number="TEST",
            valid_from=f.day,
            valid_until=f.day + timedelta(days=100),
            sku_code="FIRST-SALE",
            base_unit="支",
            qty_scale=0,
        ),
        str(uuid4()),
    )
    sku, pid = UUID(product["sku_id"]), UUID(product["id"])
    f.catalog.review(
        f.reviewer,
        "product",
        pid,
        Review(expected_version="1", decision="APPROVE", evidence_ref="TEST", reason="独立审核"),
        str(uuid4()),
    )
    customer = UUID(f.party["id"])
    f.world.execute("UPDATE med.party SET kind='BOTH' WHERE id=:id", id=customer)
    service = SalesService(f.world.uow)
    service.set_credit(
        f.maker,
        f.world.entity,
        customer,
        CreditSet(
            expected_version="0",
            credit_limit="1000",
            valid_until=f.day + timedelta(days=90),
            reason="独立信用核定",
        ),
        str(uuid4()),
    )
    body = SalesCreate(
        entity_id=f.world.entity,
        customer_id=customer,
        warehouse_id=f.world.warehouse,
        delivery_address="测试地址",
        lines=[{"sku_id": sku, "qty": "5", "unit_price": "20"}],
    )

    def snapshot(channel="wholesale"):
        return SaleStockQueries(f.world.uow).lookup(
            f.maker, channel, f.world.entity, f.world.warehouse, [sku]
        )["items"][0]

    def approved():
        oid = UUID(service.create(f.maker, body, str(uuid4()))["id"])
        service.transition(
            f.reviewer, oid, Transition(expected_version="1", reason="独立审核"), str(uuid4()), True
        )
        return oid

    return SimpleNamespace(**locals())


def test_new_product_can_be_ordered_approved_and_replenished_without_inventing_stock(fresh):
    x, f = fresh, fresh.f
    baseline = {
        table: f.world.scalar(f"SELECT count(*) FROM med.{table}")
        for table in (
            "stock_balance",
            "cost_balance",
            "stock_ledger",
            "payable_obligation",
            "shipment",
            "receivable_obligation",
        )
    }
    assert str(x.sku) in {str(r["id"]) for r in x.service.options(f.maker, f.world.entity)["skus"]}
    assert x.snapshot()["sale_eligible"] is True
    assert x.snapshot()["available_qty"] == "0.000000"
    oid = x.approved()
    assert f.world.scalar("SELECT count(*) FROM med.stock_gate WHERE sku_id=:s", s=x.sku) == 0
    repl = ReplenishmentService(f.world.uow)
    assert repl.detail(f.maker, oid)["items"][0]["suggested_qty"] == "5.000000"
    doc = x.service.detail(f.maker, oid)
    request = ShipmentCreate(
        expected_version=doc["order"]["version"],
        client_operation_id=uuid4(),
        lines=[{"order_line_id": doc["lines"][0]["id"], "qty": "1"}],
    )
    with pytest.raises(BusinessError) as error:
        allocate(x.service, f.maker, oid, request, str(uuid4()))
    assert error.value.code == "SKU_BLOCKED"
    repl.create(
        f.maker,
        oid,
        ReplenishmentCreate(
            expected_version="2",
            supplier_id=x.customer,
            lines=[{"sku_id": x.sku, "qty": "5", "unit_price": "13"}],
        ),
        str(uuid4()),
    )
    assert repl.detail(f.maker, oid)["items"][0]["suggested_qty"] == "0.000000"
    with pytest.raises(BusinessError) as error:
        allocate(x.service, f.maker, oid, request, str(uuid4()))
    assert error.value.code == "INSUFFICIENT_STOCK"
    for table, count in baseline.items():
        assert f.world.scalar(f"SELECT count(*) FROM med.{table}") == count
    for table in ("stock_balance", "cost_balance"):
        assert f.world.scalar(f"SELECT count(*) FROM med.{table} WHERE sku_id=:s", s=x.sku) == 0


@pytest.mark.parametrize(
    "change", ["gate", "draft", "expired", "serial", "no_lot", "customer_scope"]
)
def test_first_order_does_not_bypass_admission(fresh, change):
    x, f = fresh, fresh.f
    if change == "gate":
        f.world.execute(
            """INSERT INTO med.stock_gate
            (tenant_id,id,created_by,updated_by,entity_id,sku_id,blocked)
            VALUES(:t,:id,:a,:a,:e,:s,true)""",
            t=f.maker.tenant_id,
            id=uuid4(),
            a=f.maker.principal_id,
            e=f.world.entity,
            s=x.sku,
        )
    else:
        sql = {
            "draft": "UPDATE med.product SET approval_state='DRAFT' WHERE id=:p",
            "expired": "UPDATE med.product SET valid_until=:day WHERE id=:p",
            "serial": "UPDATE med.sku SET serial_required=true WHERE id=:s",
            "no_lot": "UPDATE med.sku SET lot_required=false WHERE id=:s",
            "customer_scope": "UPDATE med.party SET categories=ARRAY['DRUG'] WHERE id=:c",
        }[change]
        f.world.execute(sql, p=x.pid, s=x.sku, c=x.customer, day=f.day - timedelta(days=1))
    with pytest.raises(BusinessError) as error:
        x.service.create(f.maker, x.body, str(uuid4()))
    assert error.value.code == ("SKU_BLOCKED" if change == "gate" else "PRODUCT_INELIGIBLE")
    if change != "customer_scope":
        assert x.snapshot()["sale_eligible"] is False
        assert str(x.sku) not in {
            str(r["id"]) for r in x.service.options(f.maker, f.world.entity)["skus"]
        }


def test_new_product_can_be_revised_and_reapproved_before_first_purchase(fresh):
    x, f = fresh, fresh.f
    oid = x.approved()
    payload = x.body.model_dump(exclude={"entity_id"})
    payload["lines"][0]["qty"] = "7"
    revise(
        x.service,
        f.maker,
        oid,
        SalesRevision(expected_version="2", reason="更正首次需求", **payload),
        str(uuid4()),
    )
    doc = x.service.detail(f.maker, oid)
    assert doc["order"]["state"] == "DRAFT"
    x.service.transition(
        f.reviewer,
        oid,
        Transition(expected_version=doc["order"]["version"], reason="重新审核"),
        str(uuid4()),
        True,
    )
    assert (
        ReplenishmentService(f.world.uow).detail(f.maker, oid)["items"][0]["suggested_qty"]
        == "7.000000"
    )


def test_new_product_cannot_be_retail_sold_without_stock(fresh):
    x, f = fresh, fresh.f
    assert x.snapshot("retail")["sale_eligible"] is False
    retail = RetailService(f.world.uow)
    with pytest.raises(BusinessError) as error:
        retail.create(
            f.maker,
            RetailCreate(
                client_operation_id=uuid4(),
                entity_id=f.world.entity,
                warehouse_id=f.world.warehouse,
                lines=[{"sku_id": x.sku, "qty": "1", "unit_price": "20"}],
            ),
            str(uuid4()),
        )
    assert error.value.code == "SKU_BLOCKED"
    assert f.world.scalar("SELECT count(*) FROM med.retail_sale") == 0


def test_freeze_after_first_order_still_blocks_approval(fresh):
    x, f = fresh, fresh.f
    oid = UUID(x.service.create(f.maker, x.body, str(uuid4()))["id"])
    f.world.execute(
        """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id,blocked)
        VALUES(:t,:id,:a,:a,:e,:s,true)""",
        t=f.maker.tenant_id,
        id=uuid4(),
        a=f.maker.principal_id,
        e=f.world.entity,
        s=x.sku,
    )
    with pytest.raises(BusinessError) as error:
        x.service.transition(
            f.reviewer, oid, Transition(expected_version="1", reason="独立审核"), str(uuid4()), True
        )
    assert error.value.code == "SKU_BLOCKED"
    assert x.service.detail(f.maker, oid)["order"]["state"] == "DRAFT"
