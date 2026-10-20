from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO, Text

Category = Literal["DRUG", "DEVICE"]


class RequiredWhen(DTO):
    field: Literal["device_class", "drug_sale_class"]
    equals: Literal["I", "II", "III", "OTC", "RX"]


class FieldRule(DTO):
    key: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    display_name: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, max_length=40, pattern=r"^[^\x00-\x1f\x7f]*$"),
    ] = ""
    visible: StrictBool = True
    required: StrictBool = False
    order: Annotated[int, Field(strict=True, ge=0, le=999)]
    min_length: Annotated[int, Field(strict=True, ge=0, le=500)] = 0
    max_length: Annotated[int, Field(strict=True, ge=0, le=500)] = 0
    validator: Literal["NONE", "NO_WHITESPACE", "ASCII_CODE", "DIGITS"] = "NONE"
    required_when: list[RequiredWhen] = Field(default_factory=list, max_length=5)


class FieldRulesUpdate(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    fields: list[FieldRule] = Field(min_length=1, max_length=30)
    reason: Text
