"""Independently verify the fixed multi-product partial fulfillment trial, read-only."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dev import ROOT, environment
from sqlalchemy import create_engine, text


def verify():
    proof = json.loads(
        (ROOT / "artifacts/partial-fulfillment-reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    params = {
        "entity": UUID(proof["entity_id"]),
        "sale": UUID(proof["sales"]["id"]),
        "account": UUID(proof["account_id"]),
        "party": UUID(proof["party_id"]),
    }
    actors = {k: UUID(v["principal_id"]) for k, v in proof["actors"].items()}
    expected = {
        UUID(g["sku_id"]): (10, 100, 0) if i == 0 else (8, 160, 1)
        for i, g in enumerate(proof["goods"])
    }
    engine = create_engine(
        environment()["MED_DATABASE_URL"],
        hide_parameters=True,
        isolation_level="REPEATABLE READ",
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": proof["tenant_id"]},
            )
            conn.execute(text("SET LOCAL statement_timeout='10s'"))
            assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            role = (
                conn.execute(
                    text("""SELECT r.rolsuper,r.rolbypassrls,
                pg_has_role(current_user,n.nspowner,'MEMBER') AS owns_schema
                FROM pg_roles r CROSS JOIN pg_namespace n
                WHERE r.rolname=current_user AND n.nspname='med'""")
                )
                .mappings()
                .one()
            )
            assert not any(role.values()), "Acceptance reader must obey tenant RLS"

            def rows(sql, **extra):
                return [
                    dict(r)
                    for r in conn.execute(text(sql), {**params, **extra}).mappings()
                ]

            stocks = rows(
                "SELECT sku_id,on_hand,reserved,blocked FROM med.stock_balance WHERE entity_id=:entity"
            )
            pools = rows(
                "SELECT sku_id,qty,amount FROM med.cost_balance WHERE entity_id=:entity"
            )
            assert (
                {r["sku_id"] for r in stocks}
                == {r["sku_id"] for r in pools}
                == set(expected)
            )
            assert len(stocks) == len(pools) == 2
            assert all(
                r["on_hand"] == r["reserved"] == r["blocked"] == 0 for r in stocks
            )
            assert all(r["qty"] == r["amount"] == 0 for r in pools)
            stock_ledger = rows("""SELECT b.sku_id,count(*) AS entries,sum(l.qty_delta) AS net_qty,
                sum(greatest(l.qty_delta,0)) AS received,sum(least(l.qty_delta,0)) AS shipped
                FROM med.stock_ledger l JOIN med.stock_balance b
                ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id)
                WHERE b.entity_id=:entity GROUP BY b.sku_id""")
            cost_ledger = rows("""SELECT b.sku_id,count(*) AS entries,sum(l.qty_delta) AS net_qty,
                sum(l.amount_delta) AS net_amount,sum(greatest(l.amount_delta,0)) AS incoming_cost,
                sum(least(l.amount_delta,0)) AS outgoing_cost FROM med.cost_ledger l JOIN med.cost_balance b
                ON (b.tenant_id,b.id)=(l.tenant_id,l.cost_balance_id)
                WHERE b.entity_id=:entity GROUP BY b.sku_id""")
            assert len(stock_ledger) == len(cost_ledger) == 2
            for row in stock_ledger:
                qty, _, _ = expected[row["sku_id"]]
                assert (
                    row["entries"],
                    row["net_qty"],
                    row["received"],
                    row["shipped"],
                ) == (6, 0, qty, -qty)
            for row in cost_ledger:
                _, cost, _ = expected[row["sku_id"]]
                assert (
                    row["entries"],
                    row["net_qty"],
                    row["net_amount"],
                    row["incoming_cost"],
                    row["outgoing_cost"],
                ) == (6, 0, 0, cost, -cost)
            sale = rows(
                "SELECT state,created_by,approved_by,remaining_exposure,gross_amount FROM med.sales_order WHERE id=:sale AND entity_id=:entity"
            )[0]
            assert sale == {
                "state": "FULFILLED",
                "created_by": actors["sales"],
                "approved_by": actors["quality"],
                "remaining_exposure": Decimal(0),
                "gross_amount": Decimal("440.70"),
            }
            demand = rows(
                "SELECT sku_id,requested_qty,reserved_qty,fulfilled_qty FROM med.inventory_demand_line WHERE demand_id=:sale"
            )
            assert len(demand) == 2
            for row in demand:
                assert (
                    row["requested_qty"]
                    == row["fulfilled_qty"]
                    == expected[row["sku_id"]][0]
                )
                assert row["reserved_qty"] == 0
            purchases = rows(
                "SELECT id,approval_state,fulfillment_state,source_sales_order_id,created_by,approved_by FROM med.purchase_order WHERE entity_id=:entity"
            )
            assert {str(r["id"]) for r in purchases} == {
                p["id"] for p in proof["purchases"]
            }
            assert len(purchases) == 2
            for row in purchases:
                assert row["source_sales_order_id"] == params["sale"]
                assert (row["approval_state"], row["fulfillment_state"]) == (
                    "APPROVED",
                    "RECEIVED",
                )
                assert (
                    row["created_by"] == actors["purchase"]
                    and row["approved_by"] == actors["quality"]
                )
            received = rows("""SELECT l.sku_id,sum(l.qty) AS received,sum(l.qualified_qty) AS qualified,
                sum(l.rejected_qty) AS rejected,sum(l.rejected_returned_qty) AS returned,sum(l.posted_qty) AS posted
                FROM med.receipt_line l JOIN med.receipt r ON (r.tenant_id,r.id)=(l.tenant_id,l.receipt_id)
                JOIN med.purchase_order o ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
                WHERE o.entity_id=:entity GROUP BY l.sku_id""")
            assert len(received) == 2
            for row in received:
                qty, _, reject = expected[row["sku_id"]]
                assert (
                    row["received"],
                    row["qualified"],
                    row["rejected"],
                    row["returned"],
                    row["posted"],
                ) == (qty + reject, qty, reject, reject, qty)
            receipt_actors = rows("""SELECT r.created_by FROM med.receipt r JOIN med.purchase_order o
                ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id) WHERE o.entity_id=:entity""")
            assert receipt_actors == [{"created_by": actors["warehouse"]}] * 3
            handovers = rows(
                """SELECT h.created_by,l.qty FROM med.purchase_rejected_handover h
                JOIN med.purchase_rejected_handover_line l ON (l.tenant_id,l.handover_id)=(h.tenant_id,h.id)
                WHERE h.id=:id""",
                id=UUID(proof["handover"]["id"]),
            )
            assert handovers == [{"created_by": actors["warehouse"], "qty": Decimal(1)}]
            shipments = rows(
                "SELECT id,state,created_by,picked_by,verified_by FROM med.shipment WHERE order_id=:sale"
            )
            assert len(shipments) == 3
            for row in shipments:
                assert row["state"] == "POSTED" and row["created_by"] == actors["sales"]
                assert (
                    row["picked_by"] == actors["warehouse"]
                    and row["verified_by"] == actors["quality"]
                )
            settlements = rows(
                "SELECT kind,gross_amount,settled_amount,outstanding_amount FROM med.settlement_item WHERE entity_id=:entity AND party_id=:party"
            )
            assert len(settlements) == 7
            for kind, amounts in [
                ("AP", ["11.30", "22.60", "79.10", "180.80"]),
                ("AR", ["101.70", "152.55", "186.45"]),
            ]:
                actual = [r for r in settlements if r["kind"] == kind]
                assert sorted(r["gross_amount"] for r in actual) == sorted(
                    map(Decimal, amounts)
                )
                assert all(
                    r["gross_amount"] == r["settled_amount"]
                    and r["outstanding_amount"] == 0
                    for r in actual
                )
            cash = rows(
                "SELECT direction,amount,allocated_amount,refunded_amount,state,created_by FROM med.cash_document WHERE entity_id=:entity AND account_id=:account"
            )
            assert len(cash) == 2
            assert {r["direction"]: r["amount"] for r in cash} == {
                "RECEIPT": Decimal("440.70"),
                "PAYMENT": Decimal("293.80"),
            }
            assert all(
                r["amount"] == r["allocated_amount"]
                and r["refunded_amount"] == 0
                and r["state"] == "CONFIRMED"
                and r["created_by"] == actors["finance"]
                for r in cash
            )
            credit = rows(
                "SELECT exposure,receivable FROM med.credit_account WHERE entity_id=:entity AND customer_id=:party"
            )
            assert credit == [{"exposure": Decimal(0), "receivable": Decimal(0)}]
            result = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "run": proof["run"],
                "tenant_name": proof["tenant_name"],
                "reader": "med_runtime; tenant RLS; read-only repeatable-read",
                "stock": stocks,
                "cost": pools,
                "stock_ledger": stock_ledger,
                "cost_ledger": cost_ledger,
                "sales": sale,
                "demand": demand,
                "purchases": purchases,
                "receipts": received,
                "shipments": shipments,
                "handovers": handovers,
                "settlements": settlements,
                "cash_documents": cash,
                "credit": credit,
                "virtual_cash_record_net_change": "146.90",
            }
        (ROOT / "artifacts/partial-fulfillment-db.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            "Partial fulfillment verified: two linked purchases, three shipments; stock/cost/AR/AP zero."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
