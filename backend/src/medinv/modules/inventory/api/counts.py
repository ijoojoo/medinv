from typing import Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.inventory.domain.decimal_values import parse_unsigned_decimal
from medinv.modules.procurement.api.schemas import Transition


class CountCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    warehouse_id: UUID
    balance_ids: list[UUID] = Field(min_length=1, max_length=200)
    reason: Text

    @field_validator("balance_ids")
    @classmethod
    def unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate balance")
        return value


class CountReading(DTO):
    line_id: UUID
    actual_qty: StrictStr
    lot_no: Text
    gain_unit_cost: StrictStr | None = None
    note: Text

    @field_validator("actual_qty")
    @classmethod
    def qty(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=20, scale=6), "f")

    @field_validator("gain_unit_cost")
    @classmethod
    def cost(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cost = parse_unsigned_decimal(value, precision=24, scale=8)
        if cost <= 0:
            raise ValueError("positive gain unit cost required")
        return format(cost, "f")


class CountSubmit(Transition):
    lines: list[CountReading] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique(self) -> CountSubmit:
        if len({r.line_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate count line")
        return self


class CountReview(Transition):
    decision: Literal["APPROVE", "REJECT"]
