from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from medinv.modules.returns.api.schemas import ReturnCreate
from medinv.modules.returns.application.manifest import prepare


@pytest.mark.parametrize(
    "gross,net,cost,qty,scale",
    [
        ("0.02", "0.01", "0.01000000", "3", 0),
        ("1.00", "0.89", "0.98765432", "7", 0),
        ("0.00", "0.00", "1.12345678", "0.000003", 6),
        ("999999999999999999.99", "884955752212389380.52", "9999999999999999.99999999", "3", 0),
    ],
)
def test_cumulative_money_and_cost_finish_exactly_without_negative_tax(
    gross, net, cost, qty, scale
):
    gross, net, cost, qty = map(Decimal, (gross, net, cost, qty))
    count = 3 if scale else int(qty)
    unit = Decimal(".000001") if scale else Decimal(1)
    line = {
        "id": uuid4(),
        "base_qty": qty,
        "returned_qty": Decimal(0),
        "remaining_qty": qty,
        "gross_amount": gross,
        "net_amount": net,
        "amount_delta": -cost,
        "package_snapshot": {"qty_scale": scale},
    }
    total_g = total_n = total_c = Decimal(0)
    for _ in range(count):
        rows, g, n = prepare(
            {"gross_amount": gross, "net_amount": net},
            [line],
            ReturnCreate(
                client_operation_id=uuid4(),
                source_posting_id=uuid4(),
                reason="分次退货",
                lines=[{"source_posting_line_id": line["id"], "qty": format(unit, "f")}],
            ),
        )
        assert 0 <= n <= g
        total_g += g
        total_n += n
        total_c += rows[0]["original_cost"]
        line["returned_qty"] += unit
        line["remaining_qty"] -= unit
    assert (total_g, total_n, total_c) == (gross, net, cost)


@pytest.mark.parametrize("qty", [1.0, "1e2", "0", "0.0000001"])
def test_return_quantity_rejects_float_scientific_zero_and_excess_scale(qty):
    with pytest.raises(ValidationError):
        ReturnCreate(
            client_operation_id=uuid4(),
            source_posting_id=uuid4(),
            reason="退货",
            lines=[{"source_posting_line_id": uuid4(), "qty": qty}],
        )
