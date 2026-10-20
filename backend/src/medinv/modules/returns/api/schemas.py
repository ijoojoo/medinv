from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version
from medinv.modules.inventory.domain.decimal_values import canonical_quantity
from medinv.modules.settlement.api.schemas import CashCreate


class ReturnRequestLine(DTO):
    source_posting_line_id: UUID
    qty: StrictStr

    @field_validator("qty")
    @classmethod
    def quantity(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class ReturnCreate(DTO):
    client_operation_id: UUID
    source_posting_id: UUID
    reason: Text
    lines: list[ReturnRequestLine] | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self) -> ReturnCreate:
        if self.lines and len({r.source_posting_line_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate source posting line")
        return self


class ReturnCheckLine(ReturnRequestLine):
    lot_no: Text


class CustodyHandover(DTO):
    expected_version: Version
    reason: Text
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("physical handover must be confirmed")
        return value


class ReturnReceive(CustodyHandover):
    zone_label: Text
    lines: list[ReturnCheckLine] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self) -> ReturnReceive:
        if len({r.source_posting_line_id for r in self.lines}) != len(self.lines):
            raise ValueError("duplicate source posting line")
        return self


class ReturnReview(DTO):
    expected_version: Version
    reason: Text
    decision: Literal["APPROVE", "REJECT"]
    lines: list[ReturnCheckLine] = Field(max_length=500)

    @model_validator(mode="after")
    def unique(self) -> ReturnReview:
        if len({line.source_posting_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate source posting line")
        return self


class RefundCreate(CashCreate):
    cash_document_id: UUID
    return_document_id: UUID
    expected_cash_version: Version
