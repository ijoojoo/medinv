from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from medinv.modules.catalog.api.packaging import PackagingCreate
from medinv.modules.catalog.application.packaging import convert
from medinv.modules.procurement.api.schemas import OrderLine


@pytest.mark.parametrize(
    "raw,n,d,scale,expected",
    [
        ("2", 12, 1, 0, "24"),
        ("0.5", 12, 1, 0, "6"),
        ("3", 1, 3, 0, "1"),
        ("1", 1, 8, 3, "0.125"),
        ("99999999999999.999999", 1, 1, 6, "99999999999999.999999"),
    ],
)
def test_exact_conversion(raw, n, d, scale, expected):
    assert convert(raw, n, d, scale) == Decimal(expected)


@pytest.mark.parametrize(
    "raw,n,d,scale",
    [
        ("1", 1, 3, 6),
        ("0.1", 12, 1, 0),
        ("1", 1, 8, 2),
        ("99999999999999", 2, 1, 0),
        ("0", 12, 1, 0),
    ],
)
def test_conversion_never_rounds_or_overflows(raw, n, d, scale):
    with pytest.raises(ValueError):
        convert(raw, n, d, scale)


def test_optional_packaging_preserves_legacy_command_fingerprint():
    values = dict(sku_id=str(uuid4()), qty="1", unit_price="2", tax_rate="0.13")
    original = OrderLine(**values).model_dump(mode="json")
    assert set(original) == set(values)
    assert OrderLine(**values, packaging_id=None).model_dump(mode="json") == original
    pid = uuid4()
    assert OrderLine(**values, packaging_id=pid).model_dump(mode="json")["packaging_id"] == str(pid)


@pytest.mark.parametrize("value", [1, "0", "-1", "1.5", "1000000000", "1e3"])
def test_factor_requires_bounded_positive_integer_string(value):
    with pytest.raises(ValidationError):
        PackagingCreate(
            expected_sku_version="1",
            unit_name="箱",
            numerator=value,
            denominator="1",
            reason="核验",
        )
