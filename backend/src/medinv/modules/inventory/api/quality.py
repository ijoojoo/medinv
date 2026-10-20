from typing import Literal
from uuid import UUID

from pydantic import StrictStr, field_validator

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.inventory.domain.decimal_values import canonical_quantity
from medinv.modules.procurement.api.schemas import Transition


class DispositionCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    balance_id: UUID
    kind: Literal["RELEASE", "WRITE_OFF"]
    qty: StrictStr
    lot_no: Text
    reason: Text
    evidence: Text

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class DispositionReview(Transition):
    decision: Literal["APPROVE", "REJECT"]
