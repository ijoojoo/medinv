from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, StringConstraints, model_validator

from medinv.modules.catalog.api.certificates import CertificateType
from medinv.modules.catalog.api.party_certificates import PartyCertificateType
from medinv.modules.catalog.api.schemas import DTO, Text, Version

SubjectType = Literal["DISTRIBUTOR", "REGISTRANT", "MEDICAL_INSTITUTION", "OTHER"]
Channel = Literal["PURCHASE", "WHOLESALE"]
Code = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]


class ScopeException(DTO):
    rule_id: Text
    evidence_ref: Text
    reason: Text


class BusinessScope(DTO):
    id: UUID
    category: Literal["DRUG", "DEVICE"]
    name: Text
    device_class: Literal["I", "II", "III"] | None = None
    directory_version: Code = "2017"
    codes: list[Code] = Field(default_factory=list, max_length=200)
    excluded_codes: list[Code] = Field(default_factory=list, max_length=200)
    include_children: StrictBool = True
    subject_type: SubjectType = "DISTRIBUTOR"
    channels: list[Channel] = Field(min_length=1, max_length=2)
    certificate_ids: list[UUID] = Field(default_factory=list, max_length=100)
    exceptions: list[ScopeException] = Field(default_factory=list, max_length=30)
    enabled: StrictBool = True
    note: Annotated[StrictStr, StringConstraints(max_length=1000)] = ""

    @model_validator(mode="after")
    def valid_scope(self) -> BusinessScope:
        if self.category == "DEVICE" and (not self.device_class or not self.codes):
            raise ValueError("器械范围须填写管理类别和证照覆盖的分类编码")
        if self.category == "DRUG" and (self.device_class or self.codes or self.excluded_codes):
            raise ValueError("药品范围不填写器械管理类别或分类编码")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("适用业务不可重复")
        if len(set(self.certificate_ids)) != len(self.certificate_ids):
            raise ValueError("关联证件不可重复")
        if len({e.rule_id for e in self.exceptions}) != len(self.exceptions):
            raise ValueError("同一规则的例外依据不可重复")
        return self


class ScopeProfile(DTO):
    scopes: list[BusinessScope] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_scopes(self) -> ScopeProfile:
        if len({s.id for s in self.scopes}) != len(self.scopes):
            raise ValueError("经营范围身份不可重复")
        return self


class ScopeSave(DTO):
    expected_version: Version
    payload: ScopeProfile
    reason: Text


class BundleDiscard(DTO):
    expected_version: Version
    reason: Text


class QualificationRule(DTO):
    id: Text
    name: Text
    enabled: StrictBool = True
    category: Literal["DRUG", "DEVICE"]
    device_classes: list[Literal["I", "II", "III"]] = Field(default_factory=list, max_length=3)
    subjects: list[SubjectType] = Field(min_length=1, max_length=4)
    channels: list[Channel] = Field(min_length=1, max_length=2)
    certificate_groups: list[list[PartyCertificateType]] = Field(
        default_factory=list, max_length=10
    )
    source_title: Text
    source_url: Annotated[StrictStr, StringConstraints(pattern=r"^https://", max_length=500)]
    article: Text
    effective_from: date
    effective_until: date | None = None
    checked_on: date
    note: Annotated[StrictStr, StringConstraints(max_length=1000)] = ""

    @model_validator(mode="after")
    def groups(self) -> QualificationRule:
        if any(not g or len(g) != len(set(g)) for g in self.certificate_groups):
            raise ValueError("每组证件条件至少选择一种，组内不可重复")
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("规则失效日期不能早于生效日期")
        return self


class QualificationSettings(DTO):
    enforcement: Literal["BLOCK", "WARN", "OFF"] = "BLOCK"
    impact: Literal["CATEGORY", "PARTY"] = "CATEGORY"
    require_own_scope: StrictBool = True
    legacy_party_validity: StrictBool = True
    product_certificate_required: StrictBool = True
    product_certificate_dates: StrictBool = True
    product_certificate_type_check: StrictBool = True
    product_regulatory_subject_required: StrictBool = True
    certificate_evidence_required: StrictBool = True
    authorization_contact_required: StrictBool = True
    party_certificate_validity_confirmed: StrictBool = True
    product_fixed_period_types: list[CertificateType] = Field(
        default=[
            "DRUG_APPROVAL",
            "DEVICE_REGISTRATION",
            "HEALTH_REGISTRATION",
            "FSMP_REGISTRATION",
            "INFANT_FORMULA_REGISTRATION",
            "COSMETIC_REGISTRATION",
        ],
        max_length=9,
    )
    rules: list[QualificationRule] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_rules(self) -> QualificationSettings:
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("规则标识不可重复")
        return self


class RulesSave(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    payload: QualificationSettings
    reason: Text
