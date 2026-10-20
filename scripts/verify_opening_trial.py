"""Read-only persisted-ledger check for the fixed seven-role opening acceptance.

This is an acceptance fixture verifier, not a general financial report. Credentials
stay in the existing private runtime configuration and are never written to proof.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dev import ROOT, environment
from sqlalchemy import create_engine, text


def verify():
    proof = json.loads(
        (ROOT / "artifacts/enterprise-opening-reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    engine = create_engine(
        environment()["MED_DATABASE_URL"],
        hide_parameters=True,
        isolation_level="REPEATABLE READ",
    )
    params = {
        "tenant": UUID(proof["tenant_id"]),
        "entity": UUID(proof["entity_id"]),
        "sku": UUID(proof["sku_id"]),
        "party": UUID(proof["party_id"]),
        "account": UUID(proof["account_id"]),
    }
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

            stock = rows("""SELECT b.warehouse_id,l.lot_no,b.on_hand,b.reserved,b.blocked
                FROM med.stock_balance b JOIN med.lot l
                ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                WHERE b.entity_id=:entity ORDER BY l.lot_no""")
            lots = proof["lots"]
            expected = {
                lots["early"]: (proof["warehouse_id"], 0, "0"),
                lots["late"]: (proof["warehouse_id"], 4, "60"),
                lots["purchased"]: (proof["empty_warehouse_id"], 2, "23.01"),
            }
            assert len(stock) == 3
            for row in stock:
                warehouse, qty, _ = expected[row["lot_no"]]
                assert str(row["warehouse_id"]) == warehouse and row["on_hand"] == qty
                assert row["reserved"] == row["blocked"] == 0
            pools = rows("""SELECT c.id,l.lot_no,c.qty,c.amount FROM med.cost_balance c
                JOIN med.lot l ON (l.tenant_id,l.id)=(c.tenant_id,c.lot_id)
                WHERE c.entity_id=:entity ORDER BY l.lot_no""")
            assert len(pools) == 3
            for row in pools:
                _, qty, cost = expected[row["lot_no"]]
                assert row["qty"] == qty and row["amount"] == Decimal(cost)
                amounts = rows(
                    """SELECT sum(qty_delta) AS qty,sum(amount_delta) AS cost
                    FROM med.cost_ledger WHERE cost_balance_id=:pool""",
                    pool=row["id"],
                )[0]
                assert amounts == {"qty": row["qty"], "cost": row["amount"]}
            physical = rows("""SELECT count(*) AS entries,sum(l.qty_delta) AS qty
                FROM med.stock_ledger l JOIN med.stock_balance b
                ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id) WHERE b.entity_id=:entity""")[
                0
            ]
            costs = rows("""SELECT count(*) AS entries,sum(l.qty_delta) AS qty,
                sum(l.amount_delta) AS amount FROM med.cost_ledger l JOIN med.cost_balance c
                ON (c.tenant_id,c.id)=(l.tenant_id,l.cost_balance_id) WHERE c.entity_id=:entity""")[
                0
            ]
            assert physical == {"entries": 6, "qty": Decimal(6)}
            assert costs == {
                "entries": 6,
                "qty": Decimal(6),
                "amount": Decimal("83.01"),
            }
            openings = rows("""SELECT id,state,created_by,reviewed_by,warehouse_id
                FROM med.inventory_opening WHERE entity_id=:entity""")
            assert len(openings) == 1
            opening = openings[0]
            assert (
                str(opening["id"]) == proof["opening_id"]
                and opening["state"] == "POSTED"
            )
            assert (
                str(opening["created_by"])
                == proof["actors"]["warehouse"]["principal_id"]
            )
            assert (
                str(opening["reviewed_by"])
                == proof["actors"]["quality"]["principal_id"]
            )
            assert opening["created_by"] != opening["reviewed_by"]
            assert str(opening["warehouse_id"]) == proof["warehouse_id"]
            for table, actor, key in [
                ("purchase_order", "purchase", "purchase_id"),
                ("sales_order", "sales", "sales_id"),
                ("retail_sale", "cashier", "retail_id"),
            ]:
                row = rows(
                    f"SELECT created_by FROM med.{table} WHERE entity_id=:entity AND id=:id",
                    id=UUID(proof[key]),
                )[0]
                assert str(row["created_by"]) == proof["actors"][actor]["principal_id"]
            assert rows(
                "SELECT state FROM med.retail_sale WHERE entity_id=:entity"
            ) == [{"state": "COMPLETED"}]
            assert (
                rows("""SELECT s.state FROM med.shipment s JOIN med.sales_order o
                ON (o.tenant_id,o.id)=(s.tenant_id,s.order_id) WHERE o.entity_id=:entity""")
                == [{"state": "POSTED"}]
            )
            settlements = rows("""SELECT kind,gross_amount,settled_amount,outstanding_amount
                FROM med.settlement_item WHERE entity_id=:entity AND party_id=:party ORDER BY kind""")
            assert len(settlements) == 2
            for row, kind, gross in zip(
                settlements, ["AP", "AR"], [26, 60], strict=True
            ):
                assert row == {
                    "kind": kind,
                    "gross_amount": Decimal(gross),
                    "settled_amount": Decimal(gross),
                    "outstanding_amount": Decimal(0),
                }
            cash = rows("""SELECT direction,amount,allocated_amount,created_by,state
                FROM med.cash_document WHERE entity_id=:entity AND account_id=:account""")
            assert len(cash) == 2
            assert {r["direction"]: r["amount"] for r in cash} == {
                "RECEIPT": Decimal(60),
                "PAYMENT": Decimal(26),
            }
            assert all(
                r["state"] == "CONFIRMED"
                and r["amount"] == r["allocated_amount"]
                and str(r["created_by"]) == proof["actors"]["finance"]["principal_id"]
                for r in cash
            )
            retail = rows("""SELECT amount FROM med.retail_payment
                WHERE entity_id=:entity AND account_id=:account""")
            assert retail == [{"amount": Decimal(60)}]
            result = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "run": proof["run"],
                "tenant_name": proof["tenant_name"],
                "transaction": "read only, repeatable read",
                "role": "med_runtime; tenant RLS enforced",
                "stock": stock,
                "cost_pools": pools,
                "stock_ledger": physical,
                "cost_ledger": costs,
                "opening": opening,
                "settlements": settlements,
                "cash_documents": cash,
                "retail_receipts": retail,
                "cash_record_net_change": "94.00",
                "interpretation": "Virtual acceptance account records only, not bank balance or general ledger.",
            }
        (ROOT / "artifacts/enterprise-opening-db.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            "Opening reconciliation passed: stock 6, cost 83.01, AR/AP 0; six stock/cost entries."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
