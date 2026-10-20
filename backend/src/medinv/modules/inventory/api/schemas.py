from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from medinv.modules.inventory.domain.decimal_values import canonical_quantity

Version = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,18}$")]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReservationLine(StrictDTO):
    source_line_id: UUID
    sku_id: UUID
    base_qty: StrictStr

    @field_validator("base_qty")
    @classmethod
    def normalize_quantity(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class ReserveRequest(StrictDTO):
    client_operation_id: UUID
    entity_id: UUID
    warehouse_id: UUID
    source_type: Literal["SALES_ORDER", "RETAIL_SALE", "PURCHASE_RETURN"]
    source_id: UUID
    expected_source_version: Version
    lines: list[ReservationLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_lines(self) -> ReserveRequest:
        if len({line.source_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("source_line_id must be unique")
        self.lines.sort(key=lambda line: str(line.source_line_id))
        return self


class ReleaseRequest(StrictDTO):
    expected_version: Version
    reason: Annotated[StrictStr, Field(min_length=1, max_length=500)]

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason is required")
        return value
