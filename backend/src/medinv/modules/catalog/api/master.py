from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, StringConstraints

from medinv.modules.catalog.api.prices import ReferencePricesUpdate
from medinv.modules.catalog.api.schemas import DTO, Text


class BarcodeExpected(DTO):
    id: UUID
    version: Annotated[StrictStr, StringConstraints(pattern=r"^[1-9][0-9]{0,18}$")]


class BasicUnitBarcode(DTO):
    expected_entries: list[BarcodeExpected] = Field(default_factory=list, max_length=1000)
    value: (
        Annotated[
            StrictStr,
            StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f]+$"),
        ]
        | None
    ) = None


class MasterSave(DTO):
    product_id: UUID | None = None
    category_node_id: UUID
    form_revision: Annotated[int, Field(ge=0)]
    expected_version: Annotated[int, Field(ge=0)] = 0
    values: dict[str, Any] = Field(max_length=200)
    reason: Text = "维护商品主数据"
    basic_unit_barcode: BasicUnitBarcode | None = None
    reference_prices: ReferencePricesUpdate | None = None


class SchemaSave(DTO):
    draft_id: UUID | None = None
    expected_version: Annotated[int, Field(ge=0)] = 0
    base_revision: Annotated[int, Field(ge=0)]
    payload: dict[str, Any]
    reason: Text


class SchemaPublish(DTO):
    expected_version: Annotated[int, Field(ge=1)]
    preview_token: Annotated[StrictStr, StringConstraints(min_length=64, max_length=64)]
    reason: Text


class OrganizationCreate(DTO):
    name: Text
    legal_code: Annotated[StrictStr, StringConstraints(max_length=60)] = ""
    kind: Literal["ORGANIZATION", "DEPARTMENT"] = "ORGANIZATION"
    parent_id: UUID | None = None


class MasterImport(DTO):
    category_node_id: UUID
    form_revision: Annotated[int, Field(ge=0)]
    content: Annotated[StrictStr, StringConstraints(max_length=262144)]
    reason: Text = "导入商品主数据草稿"


class EntitySettingsSave(DTO):
    entity_id: UUID
    expected_version: Annotated[int, Field(ge=0)]
    enabled: StrictBool = True
    purchase_enabled: StrictBool = True
    sales_enabled: StrictBool = True
    recommendation_level: Annotated[StrictStr, StringConstraints(max_length=60)] = ""
    abc_class: Annotated[StrictStr, StringConstraints(max_length=60)] = ""
    abc_secondary_class: Annotated[StrictStr, StringConstraints(max_length=60)] = ""
    business_note: Annotated[StrictStr, StringConstraints(max_length=500)] = ""
    reason: Text
