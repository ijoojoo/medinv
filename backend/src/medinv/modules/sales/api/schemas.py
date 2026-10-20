from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version
from medinv.modules.inventory.domain.decimal_values import (
    canonical_quantity,
    parse_unsigned_decimal,
)
from medinv.modules.procurement.api.schemas import OrderLine
from medinv.modules.workspace.drafts import DraftLinked


class SalesAgreement(DraftLinked):
    customer_id: UUID
    warehouse_id: UUID
    delivery_address: Annotated[Text, Field(max_length=500)]
    minimum_shelf_days: Annotated[int, Field(strict=True, ge=0, le=3650)] = 0
    credit_days: Annotated[int, Field(strict=True, ge=0, le=365)] = 30
    lines: list[OrderLine] = Field(min_length=1, max_length=50)


class SalesCreate(SalesAgreement):
    entity_id: UUID


class SalesRevision(SalesAgreement):
    expected_version: Version
    reason: Text


class ReplenishmentCreate(DTO):
    expected_version: Version
    supplier_id: UUID
    lines: list[OrderLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def base_units(self) -> ReplenishmentCreate:
        if any(line.packaging_id is not None for line in self.lines):
            raise ValueError("replenishment uses base units")
        if len({line.sku_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate replenishment SKU")
        return self


class CreditSet(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    credit_limit: StrictStr
    valid_until: date
    reason: Text

    @field_validator("credit_limit")
    @classmethod
    def amount(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=20, scale=2), ".2f")


class AllocateLine(DTO):
    order_line_id: UUID
    qty: StrictStr

    @field_validator("qty")
    @classmethod
    def amount(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class ShipmentCreate(DTO):
    expected_version: Version
    client_operation_id: UUID
    lines: list[AllocateLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique(self) -> ShipmentCreate:
        if len({line.order_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate order line")
        self.lines.sort(key=lambda line: str(line.order_line_id))
        return self


class PickLine(DTO):
    shipment_line_id: UUID
    qty: StrictStr
    lot_no: Annotated[Text, Field(max_length=100)]

    @field_validator("qty")
    @classmethod
    def amount(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class Pick(DTO):
    expected_version: Version
    note: Annotated[Text, Field(max_length=500)]
    lines: list[PickLine] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self) -> Pick:
        if len({line.shipment_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate shipment line")
        return self


class Verify(Pick):
    shipping_ref: Annotated[Text, Field(max_length=120)]


class ShipmentPost(DTO):
    expected_version: Version
