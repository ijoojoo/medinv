from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictStr,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from medinv.modules.catalog.api.schemas import DTO, Text, Version
from medinv.modules.inventory.domain.decimal_values import (
    canonical_quantity,
    parse_unsigned_decimal,
)
from medinv.modules.workspace.drafts import DraftLinked


class OrderLine(DTO):
    sku_id: UUID
    packaging_id: UUID | None = None
    qty: StrictStr
    unit_price: StrictStr
    tax_rate: StrictStr = "0"

    @model_serializer(mode="wrap")
    def wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = dict(handler(self))
        if self.packaging_id is None:
            result.pop("packaging_id", None)
        return result

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)

    @field_validator("unit_price", "tax_rate")
    @classmethod
    def decimal(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=20, scale=6), "f")

    @model_validator(mode="after")
    def tax(self) -> OrderLine:
        if parse_unsigned_decimal(self.tax_rate, precision=20, scale=6) > 1:
            raise ValueError("tax rate exceeds 1")
        return self


class OrderCreate(DraftLinked):
    entity_id: UUID
    supplier_id: UUID
    warehouse_id: UUID
    lines: list[OrderLine] = Field(min_length=1, max_length=50)


class Transition(DTO):
    expected_version: Version
    reason: Text


OptionalNote = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=500)]


class Approval(Transition):
    reason: Annotated[OptionalNote, Field(max_length=200)] = ""
    conclusion: Annotated[Text, Field(max_length=60)] | None = None

    @model_validator(mode="after")
    def evidence(self) -> Approval:
        if not self.reason and not self.conclusion:
            raise ValueError("approval needs a conclusion or reason")
        return self

    @model_serializer(mode="wrap")
    def wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = dict(handler(self))
        if self.conclusion is None:
            result.pop("conclusion", None)
        return result


class OrderRevision(DraftLinked):
    expected_version: Version
    reason: Text
    supplier_id: UUID
    warehouse_id: UUID
    lines: list[OrderLine] = Field(min_length=1, max_length=50)


class ReceiptLine(DTO):
    order_line_id: UUID
    location_id: UUID
    qty: StrictStr
    lot_no: Annotated[Text, Field(max_length=100)]
    mfg_date: date
    expiry_date: date

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)

    @model_validator(mode="after")
    def dates(self) -> ReceiptLine:
        if self.expiry_date < self.mfg_date:
            raise ValueError("expiry precedes manufacturing")
        if self.expiry_date == date.max:
            raise ValueError("expiry must leave room for the exclusive boundary")
        return self


class ReceiptCreate(DTO):
    expected_version: Version
    shipment_ref: Annotated[Text, Field(max_length=120)]
    transport_note: Annotated[Text, Field(max_length=500)]
    lines: list[ReceiptLine] = Field(min_length=1, max_length=100)


class InspectionLine(DTO):
    receipt_line_id: UUID
    qualified_qty: StrictStr
    rejected_qty: StrictStr

    @field_validator("qualified_qty", "rejected_qty")
    @classmethod
    def decimal(cls, value: str) -> str:
        return format(parse_unsigned_decimal(value, precision=20, scale=6), ".6f")


class InspectionCreate(DTO):
    expected_version: Version
    note: OptionalNote = ""
    conclusion: Annotated[Text, Field(max_length=60)] | None = None
    lines: list[InspectionLine] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_lines(self) -> InspectionCreate:
        if not self.note and not self.conclusion:
            raise ValueError("inspection needs a conclusion or note")
        if len({line.receipt_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate receipt line")
        return self

    @model_serializer(mode="wrap")
    def wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = dict(handler(self))
        if self.conclusion is None:
            result.pop("conclusion", None)
        return result


class ReceiptPost(DTO):
    expected_version: Version
    inspection_id: UUID
    posting_segment_id: UUID


class RejectedHandoverLine(DTO):
    receipt_line_id: UUID
    qty: StrictStr

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class RejectedHandover(DTO):
    expected_version: Version
    handover_ref: Annotated[Text, Field(max_length=120)]
    recipient: Annotated[Text, Field(max_length=200)]
    reason: Text
    lines: list[RejectedHandoverLine] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_lines(self) -> RejectedHandover:
        if len({line.receipt_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate receipt line")
        return self
