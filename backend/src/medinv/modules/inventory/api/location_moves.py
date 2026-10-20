from typing import Literal
from uuid import UUID

from pydantic import StrictBool, StrictStr, field_validator

from medinv.modules.catalog.api.schemas import DTO, Text
from medinv.modules.inventory.domain.decimal_values import canonical_quantity
from medinv.modules.procurement.api.schemas import Transition


class LocationMoveCreate(DTO):
    client_operation_id: UUID
    entity_id: UUID
    balance_id: UUID
    destination_location_id: UUID
    qty: StrictStr
    lot_no: Text
    reason: Text

    @field_validator("qty")
    @classmethod
    def valid_qty(cls, value: str) -> str:
        return canonical_quantity(value, sku_scale=6)


class LocationMoveReview(Transition):
    decision: Literal["APPROVE", "REJECT"]


class LocationMovePost(Transition):
    lot_no: Text
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def checked(cls, value: bool) -> bool:
        if not value:
            raise ValueError("physical handover confirmation required")
        return value
