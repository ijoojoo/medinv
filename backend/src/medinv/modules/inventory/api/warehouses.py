from typing import Annotated
from uuid import UUID

from pydantic import Field

from medinv.modules.catalog.api.schemas import DTO, Text, Version


class LocationCreate(DTO):
    client_operation_id: UUID
    expected_version: Version
    code: Annotated[Text, Field(max_length=50)]
    name: Text
    reason: Text


class WarehouseRename(DTO):
    expected_version: Version
    name: Text
    reason: Text
