from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, model_validator

from medinv.modules.catalog.api.party_profile import PartyProfile

Text = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Code = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Version = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,18}$")]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductProfile(DTO):
    drug_business_scope: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=100)
    ] = ""
    device_business_scope: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=100)
    ] = ""
    quality_note: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=500)] = (
        ""
    )
    search_alias: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=200)] = (
        ""
    )
    trade_name: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    storage_conditions: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    manufacturer_address: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=300)
    ] = ""
    generic_name: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=200)] = (
        ""
    )
    dosage_form: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    authorization_holder: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    authorization_holder_address: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=300)
    ] = ""
    device_class: Literal["UNKNOWN", "I", "II", "III"] = "UNKNOWN"
    device_classification_code: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=30)
    ] = ""
    device_classification_version: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=30)
    ] = ""
    device_model: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=200)] = (
        ""
    )
    registrant_name: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    registrant_address: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=300)
    ] = ""

    def validate_category(self, category: str) -> None:
        if category == "DRUG" and (
            self.device_class != "UNKNOWN"
            or self.device_model
            or self.registrant_name
            or self.registrant_address
            or self.device_business_scope
        ):
            raise ValueError("药品不能填写器械专属资料")
        if category == "DEVICE" and (
            self.generic_name
            or self.dosage_form
            or self.authorization_holder
            or self.authorization_holder_address
            or self.drug_business_scope
        ):
            raise ValueError("医疗器械不能填写药品专属资料")


PROFILE_FIELDS = tuple(ProductProfile.model_fields)


class ProductCreate(ProductProfile):
    code: Code | None = None
    name: Text
    category: Literal["DRUG", "DEVICE"]
    drug_sale_class: Literal["UNKNOWN", "OTC", "RX"] = "UNKNOWN"
    manufacturer_name: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=100)
    ] = ""
    specification: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    approval_number: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=120)
    ] = ""
    valid_from: date | None = None
    valid_until: date | None = None
    sku_code: Code | None = None
    base_unit: Annotated[Text, Field(max_length=30)]
    qty_scale: Annotated[int, Field(strict=True, ge=0, le=6)] = 0

    @model_validator(mode="after")
    def dates(self) -> ProductCreate:
        if self.sku_code and not self.code:
            raise ValueError("单独指定历史 SKU 编码时需同时提供商品编码")
        self.validate_category(self.category)
        if self.category != "DRUG" and self.drug_sale_class != "UNKNOWN":
            raise ValueError("drug classification only applies to drugs")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("qualification date range is invalid")
        return self


class PartyOrganization(DTO):
    """Explicit MDM identity request; an empty object resolves at save time."""

    id: UUID | None = None


class PartyCreate(PartyProfile):
    organization: PartyOrganization | None = None
    entity_id: UUID
    code: Code
    name: Text
    credit_code: Annotated[Text, Field(max_length=32)]
    kind: Literal["SUPPLIER", "CUSTOMER", "BOTH"]
    license_no: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    valid_from: date | None = None
    valid_until: date | None = None
    categories: list[Literal["DRUG", "DEVICE"]] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def dates(self) -> PartyCreate:
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("qualification date range is invalid")
        self.categories = sorted(set(self.categories))
        return self


class Review(DTO):
    expected_version: Version
    decision: Literal["APPROVE", "SUSPEND"]
    evidence_ref: Annotated[Text, Field(max_length=120)]
    reason: Annotated[Text, Field(max_length=200)]


class QualificationRevision(DTO):
    expected_version: Version
    name: Text
    valid_from: date | None = None
    valid_until: date | None = None
    reason: Text

    @model_validator(mode="after")
    def dates(self) -> QualificationRevision:
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("qualification date range is invalid")
        return self


class ProductRevision(QualificationRevision, ProductProfile):
    manufacturer_name: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=100)
    ] = ""
    specification: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    approval_number: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=120)
    ] = ""
    drug_sale_class: Literal["UNKNOWN", "OTC", "RX"]


class PartyRevision(QualificationRevision, PartyProfile):
    kind: Literal["SUPPLIER", "CUSTOMER", "BOTH"] | None = None
    license_no: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    categories: list[Literal["DRUG", "DEVICE"]] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique_categories(self) -> PartyRevision:
        self.categories = sorted(set(self.categories))
        return self
