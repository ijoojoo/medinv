"""Read-only reconciliation of the fixed continuous-trading acceptance scenario.

Uses the normal business runtime role and tenant RLS, never migration credentials.
mainline.spec.ts uses the default paths; continuous-operations.spec.ts supplies its
own evidence paths. Both must satisfy the same quantities, ledger counts and cash
movements. This is a development acceptance check, not a general reporting API.
"""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from dev import ROOT, environment
from sqlalchemy import create_engine, text

INPUT = ROOT / "artifacts/mainline-trial-reconciliation.json"
OUTPUT = ROOT / "artifacts/mainline-trial-db.json"


def verify(input_path=INPUT, output_path=OUTPUT):
    proof = json.loads(input_path.read_text(encoding="utf-8"))
    params = {
        "tenant": UUID(proof["tenant_id"]),
        "entity": UUID(proof["entity_id"]),
        "sku": UUID(proof["sku_id"]),
        "party": UUID(proof["party_id"]),
        "account": UUID(proof["account_id"]),
        "lot": proof["lot"],
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
                {"tenant": str(params["tenant"])},
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

            product = rows("""SELECT p.name,p.manufacturer_name FROM med.sku s JOIN med.product p
                ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id) WHERE s.id=:sku""")[0]
            assert product == {
                "name": proof["product"],
                "manufacturer_name": "软件测试厂家",
            }
            assert rows(
                "SELECT id FROM med.party WHERE id=:party AND entity_id=:entity"
            )
            assert rows(
                "SELECT id FROM med.cash_account WHERE id=:account AND entity_id=:entity"
            )

            stock = rows("""SELECT b.warehouse_id,b.on_hand,b.reserved,b.blocked
                FROM med.stock_balance b JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                WHERE b.entity_id=:entity AND b.sku_id=:sku AND l.lot_no=:lot""")
            expected = {
                UUID(proof["warehouses"]["source"]["id"]): Decimal(2),
                UUID(proof["warehouses"]["destination"]["id"]): Decimal(1),
                UUID(proof["warehouses"]["transit_id"]): Decimal(0),
            }
            assert len(stock) == 3
            assert {r["warehouse_id"]: r["on_hand"] for r in stock} == expected
            assert all(r["reserved"] == r["blocked"] == 0 for r in stock)
            pool = rows("""SELECT c.id,c.qty,c.amount FROM med.cost_balance c
                JOIN med.lot l ON (l.tenant_id,l.id)=(c.tenant_id,c.lot_id)
                WHERE c.entity_id=:entity AND c.sku_id=:sku AND l.lot_no=:lot""")
            assert len(pool) == 1
            assert pool[0]["qty"] == sum(r["on_hand"] for r in stock) == Decimal(3)
            assert pool[0]["amount"] == Decimal("34.512")
            costs = rows(
                """SELECT count(*) AS entries,sum(qty_delta) AS qty,
                sum(amount_delta) AS amount FROM med.cost_ledger WHERE cost_balance_id=:id""",
                id=pool[0]["id"],
            )[0]
            assert costs["entries"] == 8
            assert (
                costs["qty"] == pool[0]["qty"] and costs["amount"] == pool[0]["amount"]
            )
            physical = rows("""SELECT count(*) AS entries,sum(sl.qty_delta) AS qty
                FROM med.stock_ledger sl JOIN med.stock_balance b
                  ON (b.tenant_id,b.id)=(sl.tenant_id,sl.balance_id)
                JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                WHERE b.entity_id=:entity AND b.sku_id=:sku AND l.lot_no=:lot""")[0]
            assert physical == {"entries": 14, "qty": Decimal(3)}

            settlements = rows("""SELECT kind,gross_amount,settled_amount,outstanding_amount
                FROM med.settlement_item WHERE entity_id=:entity AND party_id=:party ORDER BY kind""")
            assert len(settlements) == 2
            for row, kind, gross, paid in zip(
                settlements, ["AP", "AR"], [130, 80], [117, 60], strict=True
            ):
                assert row == {
                    "kind": kind,
                    "gross_amount": Decimal(gross),
                    "settled_amount": Decimal(paid),
                    "outstanding_amount": Decimal(0),
                }
            cash = rows("""SELECT direction,amount,allocated_amount,refunded_amount,state
                FROM med.cash_document WHERE entity_id=:entity AND account_id=:account ORDER BY direction""")
            assert len(cash) == 2
            assert {r["direction"]: r["amount"] for r in cash} == {
                "RECEIPT": Decimal(60),
                "PAYMENT": Decimal(117),
            }
            assert all(
                r["state"] == "CONFIRMED"
                and r["allocated_amount"] == r["amount"]
                and r["refunded_amount"] == 0
                for r in cash
            )
            receipts = rows("""SELECT direction,amount FROM med.retail_payment
                WHERE entity_id=:entity AND account_id=:account ORDER BY amount""")
            assert receipts == [
                {"direction": "RECEIPT", "amount": Decimal(20)},
                {"direction": "RECEIPT", "amount": Decimal(40)},
            ]
            refunds = rows("""SELECT amount FROM med.retail_return_refund
                WHERE entity_id=:entity AND account_id=:account""")
            assert refunds == [{"amount": Decimal(20)}]
            assert (
                rows(
                    "SELECT count(*) AS n FROM med.cash_refund WHERE account_id=:account"
                )[0]["n"]
                == 0
            )
            net_cash = sum(
                r["amount"] if r["direction"] == "RECEIPT" else -r["amount"]
                for r in cash
            )
            net_cash += sum(r["amount"] for r in receipts) - sum(
                r["amount"] for r in refunds
            )
            assert net_cash == Decimal(-17)
            states = {}
            for name, table, state in [
                ("shipment", "shipment", "POSTED"),
                ("wholesale_return", "return_document", "POSTED"),
                ("retail_return", "return_document", "POSTED"),
                ("purchase_return", "return_document", "POSTED"),
                ("retail", "retail_sale", "COMPLETED"),
                ("retail_after_count", "retail_sale", "COMPLETED"),
                ("transfer", "transfer", "RECEIVED"),
                ("count", "stock_count", "POSTED"),
            ]:
                # Table identifiers are static above; fixture values are always bound.
                states[name] = rows(
                    f"SELECT state FROM med.{table} WHERE id=:id",
                    id=UUID(proof["documents"][name]["id"]),
                )[0]["state"]
                assert states[name] == state
            result = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "run": proof["run"],
                "transaction": "read only, repeatable read",
                "role": "med_runtime; tenant RLS enforced",
                "stock": stock,
                "cost_pool": pool[0],
                "cost_ledger": costs,
                "stock_ledger": physical,
                "settlements": settlements,
                "wholesale_and_supplier_cash": cash,
                "retail_receipts": receipts,
                "retail_return_refunds": refunds,
                "cash_record_net_change": net_cash,
                "document_final_states": states,
                "interpretation": "Isolated test account movement only; not an actual bank balance or general ledger.",
            }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            "Read-only reconciliation passed: stock 3, cost 34.512, AR/AP 0, cash record net -17."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    verify(args.input, args.output)
