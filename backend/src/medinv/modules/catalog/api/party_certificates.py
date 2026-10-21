from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, StringConstraints, model_validator

from medinv.modules.catalog.api.certificates import CertificateReview, OptionalText
from medinv.modules.catalog.api.schemas import DTO, Text, Version

PartyCertificateType = Literal[
    "BUSINESS_LICENSE",
    "DRUG_BUSINESS_LICENSE",
    "DRUG_PRODUCTION_LICENSE",
    "DEVICE_BUSINESS_LICENSE",
    "DEVICE_BUSINESS_FILING",
    "DEVICE_PRODUCTION_LICENSE",
    "DEVICE_PRODUCTION_FILING",
    "MEDICAL_INSTITUTION_LICENSE",
    "AUTHORIZATION",
    "OTHER",
]
BusinessUse = Literal["SUPPLY", "PURCHASE"]


class PartyCertificateValues(DTO):
    certificate_type: PartyCertificateType
    number: Annotated[Text, Field(max_length=120)]
    holder_name: OptionalText = ""
    issuer: OptionalText = ""
    business_uses: Annotated[list[BusinessUse], Field(min_length=1, max_length=2)]
    scope_text: Annotated[OptionalText, Field(max_length=1000)] = ""
    authorized_name: Annotated[OptionalText, Field(max_length=100)] = ""
    authorized_phone: Annotated[
        StrictStr,
        StringConstraints(
            strip_whitespace=True, max_length=50, pattern=r"^[0-9+()（） .转分机-]*$"
        ),
    ] = ""
    authorization_duties: Annotated[OptionalText, Field(max_length=500)] = ""
    issued_on: date | None = None
    validity_mode: Literal["FIXED", "LONG_TERM", "NOT_STATED", "UNCONFIRMED"] = "UNCONFIRMED"
    valid_from: date | None = None
    valid_until: date | None = None
    evidence_ref: Annotated[OptionalText, Field(max_length=120)] = ""
    note: Annotated[OptionalText, Field(max_length=500)] = ""
    attachment_id: UUID | None = None

    @model_validator(mode="after")
    def validate_record(self) -> PartyCertificateValues:
        if len(self.business_uses) != len(set(self.business_uses)):
            raise ValueError("适用业务不能重复")
        self.business_uses = sorted(self.business_uses)
        if self.validity_mode == "FIXED" and not (self.valid_from and self.valid_until):
            raise ValueError("固定有效期须填写起止日期")
        if self.validity_mode != "FIXED" and (self.valid_from or self.valid_until):
            raise ValueError("非固定有效期不填写推测的起止日期")
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("有效期起始日期不能晚于截止日期")
        if self.issued_on and self.valid_until and self.issued_on > self.valid_until:
            raise ValueError("签发日期不能晚于有效期截止日期")
        if self.certificate_type != "AUTHORIZATION" and (
            self.authorized_name or self.authorized_phone or self.authorization_duties
        ):
            raise ValueError("授权联系人与授权事项仅填写在授权书记录中")
        return self


class PartyCertificateCreate(PartyCertificateValues):
    reason: Text


class PartyCertificateRevision(PartyCertificateCreate):
    expected_version: Version


class PartyCertificateReview(CertificateReview):
    pass


PARTY_CERTIFICATE_FIELDS = tuple(PartyCertificateValues.model_fields)
