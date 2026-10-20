from decimal import Decimal, localcontext

import pytest

from medinv.modules.inventory.api.schemas import ReserveRequest
from medinv.modules.inventory.domain.decimal_values import (
    canonical_quantity,
    money_for_posting,
    quantity,
)


@pytest.mark.parametrize(
    "raw",
    [
        1,
        1.2,
        True,
        None,
        "NaN",
        "Infinity",
        "1e3",
        "-1",
        "+1",
        "01",
        " 1",
        "1 ",
        "1.0000001",
        "100000000000000",
        "0",
        "",
    ],
)
def test_invalid_quantities_are_rejected(raw):
    with pytest.raises(ValueError):
        quantity(raw, sku_scale=6)


def test_decimal_math_is_independent_of_ambient_context():
    with localcontext() as ctx:
        ctx.prec = 6
        assert canonical_quantity("99999999999999.123456", sku_scale=6) == "99999999999999.123456"
        assert money_for_posting(Decimal("100000000000.005")) == Decimal("100000000000.01")
    with pytest.raises(ValueError):
        quantity("1.5", sku_scale=0)
    with pytest.raises(ValueError):
        money_for_posting(Decimal("999999999999999999.995"))


def test_numbers_are_not_coerced_to_strings():
    from uuid import uuid4

    data = {
        key: str(uuid4())
        for key in ("client_operation_id", "entity_id", "warehouse_id", "source_id")
    }
    data.update(
        source_type="SALES_ORDER",
        expected_source_version="1",
        lines=[
            {
                "source_line_id": str(uuid4()),
                "sku_id": str(uuid4()),
                "base_qty": 2,
            }
        ],
    )
    with pytest.raises(ValueError):
        ReserveRequest.model_validate(data)
