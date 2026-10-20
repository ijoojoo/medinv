"""Read-only ledger verification of the fixed seven-role mixed-packaging fixture."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dev import ROOT, environment
from sqlalchemy import create_engine, text


def verify():
    proof = json.loads(
        (ROOT / "artifacts/mixed-packaging-reconciliation.json").read_text(
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
        "party": UUID(proof["party_id"]),
        "account": UUID(proof["account_id"]),
    }
    drug, device = (g["sku_id"] for g in proof["goods"])
    expected_stock = {drug: (15, Decimal(150)), device: (5, Decimal(50))}
    packs, docs = proof["packs"], proof["documents"]
    actors = {k: v["principal_id"] for k, v in proof["actors"].items()}
    expected_lines = {
        "purchase": {
            drug: (24, "2", "271.20", packs["drug_box"]),
            device: (15, "3", "169.50", packs["device_bag"]),
        },
        "wholesale": {
            drug: (6, "2", "72", packs["drug_bundle"]),
            device: (5, "0.5", "60", packs["device_box"]),
        },
        "retail": {
            drug: (3, "3", "45", None),
            device: (5, "1", "65", packs["device_bag"]),
        },
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

            stock = rows("""SELECT b.sku_id,b.warehouse_id,l.lot_no,
                b.on_hand,b.reserved,b.blocked FROM med.stock_balance b JOIN med.lot l
                ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                WHERE b.entity_id=:entity ORDER BY b.sku_id""")
            pools = rows("""SELECT id,sku_id,qty,amount FROM med.cost_balance
                WHERE entity_id=:entity ORDER BY sku_id""")
            assert len(stock) == len(pools) == 2
            for row in stock:
                sku = str(row["sku_id"])
                assert row["on_hand"] == expected_stock[sku][0]
                assert row["reserved"] == row["blocked"] == 0
                assert str(row["warehouse_id"]) == proof["warehouse_id"]
                assert row["lot_no"] == next(
                    g["lot"] for g in proof["goods"] if g["sku_id"] == sku
                )
            for row in pools:
                assert (row["qty"], row["amount"]) == expected_stock[str(row["sku_id"])]
            physical = rows("""SELECT b.sku_id,count(*) AS entries,sum(l.qty_delta) AS qty
                FROM med.stock_ledger l JOIN med.stock_balance b
                ON (b.tenant_id,b.id)=(l.tenant_id,l.balance_id)
                WHERE b.entity_id=:entity GROUP BY b.sku_id ORDER BY b.sku_id""")
            costs = rows("""SELECT c.sku_id,count(*) AS entries,sum(l.qty_delta) AS qty,
                sum(l.amount_delta) AS amount FROM med.cost_ledger l JOIN med.cost_balance c
                ON (c.tenant_id,c.id)=(l.tenant_id,l.cost_balance_id)
                WHERE c.entity_id=:entity GROUP BY c.sku_id ORDER BY c.sku_id""")
            assert len(physical) == len(costs) == 2
            for row in physical:
                assert (
                    row["entries"] == 3
                    and row["qty"] == expected_stock[str(row["sku_id"])][0]
                )
            for row in costs:
                assert row["entries"] == 3
                assert (row["qty"], row["amount"]) == expected_stock[str(row["sku_id"])]
            products = rows("""SELECT s.id,p.category,p.drug_sale_class FROM med.sku s
                JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)""")
            assert {
                str(r["id"]): (r["category"], r["drug_sale_class"]) for r in products
            } == {drug: ("DRUG", "OTC"), device: ("DEVICE", "UNKNOWN")}
            packaging = rows("""SELECT id,sku_id,unit_name,numerator,denominator,state,
                created_by,reviewed_by FROM med.packaging ORDER BY sku_id,unit_name,revision""")
            expected_packs = {
                packs["drug_box"]: (drug, "箱", 12, "RETIRED"),
                packs["drug_bundle"]: (drug, "中包", 3, "APPROVED"),
                packs["device_box"]: (device, "箱", 10, "APPROVED"),
                packs["device_bag"]: (device, "包", 5, "RETIRED"),
                proof["replacements"]["drug_box"]: (drug, "箱", 24, "APPROVED"),
                proof["replacements"]["device_bag"]: (device, "包", 10, "APPROVED"),
            }
            assert len(packaging) == 6
            for row in packaging:
                assert (
                    str(row["sku_id"]),
                    row["unit_name"],
                    row["numerator"],
                    row["state"],
                ) == expected_packs[str(row["id"])]
                assert row["denominator"] == 1
                assert str(row["created_by"]) == actors["purchase"]
                assert str(row["reviewed_by"]) == actors["quality"]
            lines, documents = {}, {}
            for key, table, actor, parent in [
                ("purchase", "purchase_order", "purchase", "order_id"),
                ("wholesale", "sales_order", "sales", "order_id"),
                ("retail", "retail_sale", "cashier", "sale_id"),
            ]:
                doc_id = UUID(docs[key]["id"])
                documents[key] = rows(
                    f"SELECT * FROM med.{table} WHERE entity_id=:entity AND id=:id",
                    id=doc_id,
                )[0]
                assert str(documents[key]["created_by"]) == actors[actor]
                lines[key] = rows(
                    f"SELECT sku_id,qty,trade_qty,gross_amount,package_snapshot FROM med.{table}_line WHERE {parent}=:id ORDER BY line_no",
                    id=doc_id,
                )
                assert len(lines[key]) == 2
                for row in lines[key]:
                    qty, trade, gross, pack = expected_lines[key][str(row["sku_id"])]
                    assert (row["qty"], row["trade_qty"], row["gross_amount"]) == (
                        qty,
                        Decimal(trade),
                        Decimal(gross),
                    )
                    assert row["package_snapshot"].get("packaging_id") == pack
                    assert Decimal(row["package_snapshot"]["base_qty"]) == qty
            assert documents["purchase"]["approval_state"] == "APPROVED"
            assert documents["purchase"]["fulfillment_state"] == "RECEIVED"
            assert documents["wholesale"]["state"] == "FULFILLED"
            assert documents["retail"]["state"] == "COMPLETED"
            for key in ("purchase", "wholesale"):
                assert str(documents[key]["approved_by"]) == actors["quality"]
            receipt = rows(
                "SELECT created_by FROM med.receipt WHERE id=:id AND order_id=:order",
                id=UUID(docs["receipt"]["id"]),
                order=UUID(docs["purchase"]["id"]),
            )[0]
            assert str(receipt["created_by"]) == actors["warehouse"]
            shipment = rows(
                "SELECT state,created_by,picked_by,verified_by FROM med.shipment WHERE id=:id AND order_id=:order",
                id=UUID(docs["shipment"]["id"]),
                order=UUID(docs["wholesale"]["id"]),
            )[0]
            assert shipment["state"] == "POSTED"
            for column, actor in [
                ("created_by", "sales"),
                ("picked_by", "warehouse"),
                ("verified_by", "quality"),
            ]:
                assert str(shipment[column]) == actors[actor]
            postings = rows("""SELECT p.id,l.sku_id,l.base_qty,l.package_snapshot
                FROM med.posting_batch p JOIN med.posting_line l
                ON (l.tenant_id,l.posting_id)=(p.tenant_id,p.id)
                WHERE p.entity_id=:entity ORDER BY p.id,l.line_no""")
            assert len(postings) == 6
            posting_keys = {
                docs["incoming"]["posting_id"]: "purchase",
                docs["outgoing"]["posting_id"]: "wholesale",
                str(documents["retail"]["posting_id"]): "retail",
            }
            for row in postings:
                key = posting_keys[str(row["id"])]
                qty, _, _, pack = expected_lines[key][str(row["sku_id"])]
                assert (
                    row["base_qty"] == qty
                    and row["package_snapshot"].get("packaging_id") == pack
                )
            settlements = rows("""SELECT kind,gross_amount,settled_amount,outstanding_amount
                FROM med.settlement_item WHERE entity_id=:entity AND party_id=:party ORDER BY kind""")
            assert settlements == [
                {
                    "kind": k,
                    "gross_amount": Decimal(v),
                    "settled_amount": Decimal(v),
                    "outstanding_amount": Decimal(0),
                }
                for k, v in [("AP", "440.70"), ("AR", "132")]
            ]
            cash = rows("""SELECT direction,amount,allocated_amount,refunded_amount,created_by,state
                FROM med.cash_document WHERE entity_id=:entity AND account_id=:account""")
            assert len(cash) == 2
            assert {r["direction"]: r["amount"] for r in cash} == {
                "RECEIPT": Decimal(132),
                "PAYMENT": Decimal("440.70"),
            }
            assert all(
                r["state"] == "CONFIRMED"
                and r["amount"] == r["allocated_amount"]
                and r["refunded_amount"] == 0
                and str(r["created_by"]) == actors["finance"]
                for r in cash
            )
            retail = rows("""SELECT direction,amount,tendered,change_amount,created_by FROM med.retail_payment
                WHERE entity_id=:entity AND account_id=:account""")
            assert retail == [
                {
                    "direction": "RECEIPT",
                    "amount": Decimal(110),
                    "tendered": Decimal(120),
                    "change_amount": Decimal(10),
                    "created_by": UUID(actors["cashier"]),
                }
            ]
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
                "packaging": packaging,
                "order_lines": lines,
                "posting_lines": postings,
                "settlements": settlements,
                "cash_documents": cash,
                "retail_receipts": retail,
                "cash_record_net_change": str(
                    retail[0]["amount"]
                    + sum(
                        r["amount"] * (1 if r["direction"] == "RECEIPT" else -1)
                        for r in cash
                    )
                ),
                "interpretation": "Virtual acceptance account records only, not bank balance or general ledger.",
            }
            assert Decimal(result["cash_record_net_change"]) == Decimal("-198.70")
        (ROOT / "artifacts/mixed-packaging-db.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            "Mixed packaging reconciliation passed: 15 drug boxes, 5 device pieces, cost 200, AR/AP 0."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    verify()
