"""Independent runtime-role, tenant-RLS read-only reconciliation of the recovery drill."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import recovery
from dev import environment
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify(target=None):
    root = recovery.ROOT
    proof = json.loads(
        (root / "artifacts/recovery-scenario.json").read_text(encoding="utf-8")
    )
    f = proof["f"]
    if target:
        recovery.assert_target(target)
        config = json.loads(
            (recovery.target_path(target) / "compose.json").read_bytes()
        )
        url = make_url(
            config["services"]["core"]["environment"]["MED_DATABASE_URL"]
        ).set(host="127.0.0.1", port=55433)
    else:
        assert not recovery.JOURNAL.exists(), "Source is in maintenance"
        url = make_url(environment()["MED_DATABASE_URL"])
    params = {
        "tenant": UUID(proof["opened"]["tenant_id"]),
        "entity": UUID(f["entity"]),
        "sku": UUID(f["product"]["sku_id"]),
        "account": UUID(proof["account"]["id"]),
        "party": UUID(f["party"]["id"]),
        "lot": f["lot"],
    }
    engine = create_engine(url, hide_parameters=True, isolation_level="REPEATABLE READ")
    try:
        with engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"),
                {"t": str(params["tenant"])},
            )
            conn.execute(text("SET LOCAL statement_timeout='10s'"))
            flags = conn.execute(
                text(
                    "SELECT rolsuper,rolbypassrls,pg_has_role(current_user,'med_owner','MEMBER') FROM pg_roles WHERE rolname=current_user"
                )
            ).one()
            assert not any(flags)
            assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"

            def rows(query, **extra):
                return [
                    dict(row)
                    for row in conn.execute(text(query), {**params, **extra}).mappings()
                ]

            stock = rows("""SELECT b.on_hand,b.reserved,b.blocked FROM med.stock_balance b JOIN med.lot l
                ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id) WHERE b.entity_id=:entity AND b.sku_id=:sku AND l.lot_no=:lot""")
            assert len(stock) == 1
            assert stock[0] == {
                "on_hand": Decimal(9 if target else 10),
                "reserved": Decimal(0 if target else 6),
                "blocked": Decimal(0),
            }
            pool = rows("""SELECT c.id,c.qty,c.amount FROM med.cost_balance c JOIN med.lot l
                ON (l.tenant_id,l.id)=(c.tenant_id,c.lot_id) WHERE c.entity_id=:entity AND c.sku_id=:sku AND l.lot_no=:lot""")[
                0
            ]
            assert pool["qty"] == stock[0]["on_hand"]
            assert pool["amount"] == Decimal("103.536" if target else "115.04")
            cost_ledger = rows(
                "SELECT count(*) n,sum(qty_delta) qty,sum(amount_delta) amount FROM med.cost_ledger WHERE cost_balance_id=:id",
                id=pool["id"],
            )[0]
            assert cost_ledger == {
                "n": 4 if target else 1,
                "qty": pool["qty"],
                "amount": pool["amount"],
            }
            stock_ledger = rows("""SELECT count(*) n,sum(sl.qty_delta) qty FROM med.stock_ledger sl JOIN med.stock_balance b
                ON (b.tenant_id,b.id)=(sl.tenant_id,sl.balance_id) JOIN med.lot l ON (l.tenant_id,l.id)=(b.tenant_id,b.lot_id)
                WHERE b.entity_id=:entity AND b.sku_id=:sku AND l.lot_no=:lot""")[0]
            assert stock_ledger == {"n": 4 if target else 1, "qty": pool["qty"]}
            items = rows(
                "SELECT kind,count(*) n,sum(gross_amount) gross,sum(outstanding_amount) outstanding FROM med.settlement_item WHERE entity_id=:entity AND party_id=:party GROUP BY kind ORDER BY kind"
            )
            assert items == (
                [
                    {
                        "kind": "AP",
                        "n": 2,
                        "gross": Decimal(195),
                        "outstanding": Decimal(0),
                    },
                    {
                        "kind": "AR",
                        "n": 1,
                        "gross": Decimal(80),
                        "outstanding": Decimal(0),
                    },
                ]
                if target
                else [
                    {
                        "kind": "AP",
                        "n": 1,
                        "gross": Decimal(130),
                        "outstanding": Decimal(130),
                    }
                ]
            )
            retail = rows(
                "SELECT direction,amount FROM med.retail_payment WHERE entity_id=:entity AND account_id=:account"
            )
            assert retail == [{"direction": "RECEIPT", "amount": Decimal(40)}]
            cash = rows(
                "SELECT direction,amount,allocated_amount FROM med.cash_document WHERE entity_id=:entity AND account_id=:account ORDER BY direction"
            )
            assert cash == (
                [
                    {
                        "direction": "PAYMENT",
                        "amount": Decimal(195),
                        "allocated_amount": Decimal(195),
                    },
                    {
                        "direction": "RECEIPT",
                        "amount": Decimal(80),
                        "allocated_amount": Decimal(80),
                    },
                ]
                if target
                else []
            )
            report = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "target": target or "source",
                "tenant_id": str(params["tenant"]),
                "transaction": "read only, repeatable read",
                "role": "med_runtime, tenant RLS enforced",
                "stock": stock,
                "cost": pool,
                "stock_ledger": stock_ledger,
                "cost_ledger": cost_ledger,
                "settlements": items,
                "retail_payments": retail,
                "cash_documents": cash,
            }
            (
                root
                / f"artifacts/recovery-db-{'restored' if target else 'source'}.json"
            ).write_text(
                json.dumps(report, default=str, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print("只读核对通过：库存、预占、成本、流水、往来和收付款一致。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target")
    args = parser.parse_args()
    verify(args.target)
