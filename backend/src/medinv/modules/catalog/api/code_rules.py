from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO, Text

CodeCategory = Literal[
    "DRUG", "HERB", "DEVICE", "FOOD", "HEALTH", "FSMP", "INFANT", "COSMETIC", "DAILY", "SERVICE"
]


class ProductCodeRuleUpdate(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    enabled: StrictBool
    prefix: Annotated[StrictStr, StringConstraints(max_length=12, pattern=r"^[A-Za-z0-9_-]*$")]
    date_format: Literal["NONE", "YYYY", "YYYYMM", "YYYYMMDD"]
    digits: Annotated[int, Field(strict=True, ge=1, le=12)]
    start_number: Annotated[int, Field(strict=True, ge=1, le=999999999999)]
    reason: Text
