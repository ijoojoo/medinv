from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version
from medinv.modules.inventory.domain.decimal_values import (
    canonical_quantity,
    parse_unsigned_decimal,
)
from medinv.modules.procurement.api.schemas import OrderLine


class RetailCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    warehouse_id: UUID
    lines: list[OrderLine] = Field(min_length=1, max_length=50)


class RetailCashConfirmation(DTO):
    client_operation_id: UUID
    expected_version: Version
    reason: Text
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def actual_cash(cls, value: bool) -> bool:
        if not value:
            raise ValueError("actual cash exchange must be confirmed")
        return value


class RetailPayment(RetailCashConfirmation):
    account_id: UUID
    tendered: StrictStr

    @field_validator("tendered")
    @classmethod
    def money(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=20, scale=2), ".2f")


class RetailCheck(DTO):
    allocation_id: UUID
    qty: StrictStr
    lot_no: Text

    @field_validator("qty")
    @classmethod
    def amount(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class RetailComplete(DTO):
    expected_version: Version
    reason: Text
    lines: list[RetailCheck] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self) -> RetailComplete:
        if len({r.allocation_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate allocation")
        return self
