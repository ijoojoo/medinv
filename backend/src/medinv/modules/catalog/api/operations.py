from typing import Annotated, Literal

from pydantic import Field, StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO, Text, Version
from medinv.modules.catalog.domain.field_catalog import PRODUCT_DIRECT_FIELDS

MasterKind = Literal["products", "parties"]
NON_QUALIFICATION_FIELDS = {
    "product": PRODUCT_DIRECT_FIELDS,
    "party": frozenset(
        {"search_alias", "short_name", "contact_name", "contact_phone", "delivery_address"}
    ),
}
OperationalKey = Literal[
    "recommendation_level",
    "abc_class",
    "abc_secondary_class",
    "business_note",
    "search_alias",
    "short_name",
    "contact_name",
    "contact_phone",
    "delivery_address",
]


class OperationsUpdate(DTO):
    expected_version: Version
    values: dict[
        OperationalKey,
        Annotated[
            StrictStr,
            StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^[^\x00]*$"),
        ],
    ] = Field(min_length=1, max_length=9)
    reason: Text
