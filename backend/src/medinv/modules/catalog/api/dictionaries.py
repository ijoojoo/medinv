from typing import Annotated, Literal
from uuid import UUID

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
    "supplier_grade",
    "customer_grade",
    "delivery_method",
    "payment_method",
    "department",
    "staff_status",
    "maintenance_type",
]


class DictionaryItem(DTO):
    is_default: StrictBool = False
    id: UUID | None = None
    code: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")] | None = None
    description: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, max_length=300, pattern=r"^[^\x00-\x1f\x7f]*$"),
    ] = ""
    parent_id: UUID | None = None
    origin: Literal["SYSTEM", "TENANT"] | None = None
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
