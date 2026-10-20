from typing import Literal
from uuid import UUID

from pydantic import StrictStr, field_validator

from medinv.modules.catalog.api.schemas import Text
from medinv.modules.inventory.domain.decimal_values import canonical_quantity
from medinv.modules.procurement.api.schemas import Transition


class TransferLossCreate(Transition):
    client_operation_id: UUID
    line_id: UUID
    qty: StrictStr
    lot_no: Text
    evidence: Text

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class TransferLossReview(Transition):
    decision: Literal["APPROVE", "REJECT"]
