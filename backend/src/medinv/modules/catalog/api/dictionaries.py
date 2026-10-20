from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO, Text

DictionaryKey = Literal[
    "dosage_form",
    "trade_name",
    "storage_conditions",
    "base_unit",
    "recommendation_level",
    "abc_class",
    "drug_business_scope",
    "device_business_scope",
    "purchase_approval",
    "inspection_qualified",
    "inspection_mixed",
    "inspection_rejected",
]


class DictionaryItem(DTO):
    value: Annotated[
        StrictStr,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=500, pattern=r"^[^\x00-\x1f\x7f]+$"
        ),
    ]
    active: StrictBool = True
    order: Annotated[int, Field(strict=True, ge=0, le=9999)]


class DictionaryUpdate(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    mode: Literal["SUGGEST", "STRICT"]
    items: list[DictionaryItem] = Field(max_length=200)
    reason: Text
