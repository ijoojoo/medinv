from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.inventory.domain.decimal_values import canonical_quantity
from medinv.modules.procurement.api.schemas import Transition


class WarehouseCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    code: Annotated[Text, Field(max_length=50)]
    name: Text
    location_code: Annotated[Text, Field(max_length=50)]


class TransferLine(DTO):
    balance_id: UUID
    qty: StrictStr

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class TransferCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    destination_location_id: UUID
    reason: Text
    lines: list[TransferLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique(self) -> TransferCreate:
        if len({r.balance_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate source balance")
        return self


class TransferReview(Transition):
    decision: Literal["APPROVE", "REJECT"]


class TransferReceiveLine(DTO):
    line_id: UUID
    qty: StrictStr
    lot_no: Text
    quality_status: Literal["QUALIFIED", "QUARANTINE"]

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class TransferReceive(Transition):
    client_operation_id: UUID
    lines: list[TransferReceiveLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique(self) -> TransferReceive:
        if len({r.line_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate receipt line")
        return self
