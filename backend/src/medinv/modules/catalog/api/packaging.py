from typing import Annotated

from pydantic import Field, StrictStr

from medinv.modules.catalog.api.schemas import DTO, Text, Version

Ratio = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,8}$")]


class PackagingCreate(DTO):
    expected_sku_version: Version
    unit_name: Annotated[Text, Field(max_length=30)]
    numerator: Ratio
    denominator: Ratio
    reason: Text
