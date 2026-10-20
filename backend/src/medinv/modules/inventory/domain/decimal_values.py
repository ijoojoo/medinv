"""Exact decimal boundaries for inventory and CNY amounts."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

_PLAIN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


def parse_unsigned_decimal(raw: object, *, precision: int, scale: int) -> Decimal:
    if not 0 <= scale <= precision or precision < 1:
        raise ValueError("invalid numeric definition")
    if type(raw) is not str or len(raw) > precision + 2 or not _PLAIN.fullmatch(raw):
        raise ValueError("plain decimal string required")
    whole, _, fraction = raw.partition(".")
    # Zero is permitted even when the database integer digit capacity is zero.
    integer_digits = len(whole.lstrip("0"))
    if integer_digits > precision - scale or len(fraction) > scale:
        raise ValueError("decimal overflow or excess scale")
    value = Decimal(raw)
    if not value.is_finite():
        raise ValueError("finite decimal required")
    return value


def quantity(raw: object, *, sku_scale: int) -> Decimal:
    if not 0 <= sku_scale <= 6:
        raise ValueError("invalid SKU precision")
    value = parse_unsigned_decimal(raw, precision=20, scale=6)
    with localcontext() as ctx:
        ctx.prec = 50
        step = Decimal(1).scaleb(-sku_scale)
        if value <= 0 or value != value.quantize(step):
            raise ValueError("quantity must be positive and exactly representable")
    return value


def money_for_posting(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("non-negative finite Decimal required")
    with localcontext() as ctx:
        ctx.prec = 50
        try:
            result = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("money overflow") from exc
        if result >= Decimal("1000000000000000000"):
            raise ValueError("numeric(20,2) overflow")
        return result


def canonical_quantity(raw: object, *, sku_scale: int) -> str:
    with localcontext() as ctx:
        ctx.prec = 50
        return format(quantity(raw, sku_scale=sku_scale).quantize(Decimal("0.000001")), "f")


def portion(
    total: Decimal, start: Decimal, end: Decimal, denominator: Decimal, step: str = ".01"
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return (total * end / denominator).quantize(Decimal(step), rounding=ROUND_HALF_UP) - (
            total * start / denominator
        ).quantize(Decimal(step), rounding=ROUND_HALF_UP)
