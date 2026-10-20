from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from medinv.modules.catalog.api.schemas import DTO, Code, Text, Version
from medinv.modules.inventory.domain.decimal_values import parse_unsigned_decimal


def positive_money(value: str) -> str:
    amount = parse_unsigned_decimal(value, precision=20, scale=2)
    if amount <= 0:
        raise ValueError("amount must be positive")
    return format(amount, ".2f")


class AccountCreate(DTO):
    entity_id: UUID
    code: Code
    name: Text
    method: Literal["BANK", "CASH"]


class CashCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    party_id: UUID
    account_id: UUID
    direction: Literal["RECEIPT", "PAYMENT"]
    amount: StrictStr
    reference: Annotated[Text, Field(max_length=120)]
    occurred_on: date
    note: Annotated[Text, Field(max_length=500)]
    confirmed: Literal[True]

    @field_validator("amount")
    @classmethod
    def money(cls, value: str) -> str:
        return positive_money(value)

    @field_validator("confirmed", mode="before")
    @classmethod
    def actual_bool(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value


class AllocationLine(DTO):
    settlement_item_id: UUID
    expected_item_version: Version
    amount: StrictStr

    @field_validator("amount")
    @classmethod
    def money(cls, value: str) -> str:
        return positive_money(value)


class AllocateRequest(DTO):
    client_operation_id: UUID
    cash_document_id: UUID
    expected_cash_version: Version
    allocations: list[AllocationLine] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique(self) -> AllocateRequest:
        if len({line.settlement_item_id for line in self.allocations}) != len(self.allocations):
            raise ValueError("duplicate obligation")
        self.allocations.sort(key=lambda line: str(line.settlement_item_id))
        return self


class ReverseRequest(DTO):
    client_operation_id: UUID
    reason: Text
