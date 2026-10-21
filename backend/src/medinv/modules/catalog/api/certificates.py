from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, StringConstraints, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version

CertificateType = Literal[
    "DRUG_APPROVAL",
    "DEVICE_REGISTRATION",
    "DEVICE_FILING",
    "HEALTH_REGISTRATION",
    "HEALTH_FILING",
    "FSMP_REGISTRATION",
    "INFANT_FORMULA_REGISTRATION",
    "COSMETIC_FILING",
    "COSMETIC_REGISTRATION",
]
ValidityMode = Literal["FIXED", "NOT_STATED", "UNCONFIRMED"]
OptionalText = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=200)]


class CertificateValues(DTO):
    certificate_type: CertificateType
    number: Annotated[Text, Field(max_length=120)]
    holder_name: OptionalText = ""
    issuer: OptionalText = ""
    covered_models: Annotated[OptionalText, Field(max_length=500)] = ""
    issued_on: date | None = None
    validity_mode: ValidityMode = "UNCONFIRMED"
    valid_from: date | None = None
    valid_until: date | None = None
    evidence_ref: Annotated[OptionalText, Field(max_length=120)] = ""
    note: Annotated[OptionalText, Field(max_length=500)] = ""
    attachment_id: UUID | None = None

    @model_validator(mode="after")
    def dates(self) -> CertificateValues:
        if self.validity_mode == "FIXED" and not (self.valid_from and self.valid_until):
            raise ValueError("固定有效期须填写起止日期")
        if self.validity_mode != "FIXED" and (self.valid_from or self.valid_until):
            raise ValueError("未载明或待核实有效期时，不填写推测的起止日期")
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("有效期起始日期不能晚于截止日期")
        if self.issued_on and self.valid_until and self.issued_on > self.valid_until:
            raise ValueError("签发日期不能晚于有效期截止日期")
        return self


class CertificateCreate(CertificateValues):
    reason: Text


class CertificateRevision(CertificateCreate):
    expected_version: Version


class CertificateReview(DTO):
    expected_version: Version
    decision: Literal["VERIFY", "INACTIVATE"]
    evidence_ref: Annotated[Text, Field(max_length=120)]
    reason: Text


CERTIFICATE_FIELDS = tuple(CertificateValues.model_fields)
