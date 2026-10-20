from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.inventory.domain.decimal_values import (
    canonical_quantity,
    parse_unsigned_decimal,
)
from medinv.modules.procurement.api.schemas import Transition


class OpeningLine(DTO):
    sku_id: UUID
    location_id: UUID
    lot_no: Annotated[Text, Field(max_length=100)]
    mfg_date: date
    expiry_date: date
    qty: StrictStr
    cost_amount: StrictStr

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)

    @field_validator("cost_amount")
    @classmethod
    def valid_cost(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=18, scale=2), ".2f")

    @model_validator(mode="after")
    def dates(self) -> OpeningLine:
        if self.expiry_date < self.mfg_date or self.expiry_date == date.max:
            raise ValueError("invalid lot dates")
        return self


class OpeningCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    warehouse_id: UUID
    source_ref: Annotated[Text, Field(max_length=120)]
    reason: Text
    lines: list[OpeningLine] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_lines(self) -> OpeningCreate:
        keys = {(line.sku_id, line.lot_no, line.location_id) for line in self.lines}
        if len(keys) != len(self.lines):
            raise ValueError("duplicate SKU, lot and location")
        return self


class OpeningReview(Transition):
    decision: Literal["APPROVE", "REJECT"]
    evidence_ref: Annotated[Text, Field(max_length=120)]
