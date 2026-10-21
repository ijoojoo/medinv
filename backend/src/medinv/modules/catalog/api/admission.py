from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version

Channel = Literal["PURCHASE", "WHOLESALE"]


class ScopeGrant(DTO):
    category: Literal["DRUG", "DEVICE"]
    value: Annotated[Text, Field(max_length=100)]
    valid_from: date
    valid_until: date
    evidence_ref: Text

    @model_validator(mode="after")
    def dates(self) -> ScopeGrant:
        if self.valid_from > self.valid_until:
            raise ValueError("范围核验起始日期不能晚于截止日期")
        return self


class PartyScopeGrant(ScopeGrant):
    party_id: UUID
    certificate_id: UUID
    certificate_version: Version


class AdmissionPayload(DTO):
    enabled: StrictBool = False
    own_scopes: Annotated[list[ScopeGrant], Field(max_length=200)] = []
    party_scopes: Annotated[list[PartyScopeGrant], Field(max_length=1000)] = []

    @model_validator(mode="after")
    def distinct(self) -> AdmissionPayload:
        own = [(g.category, g.value) for g in self.own_scopes]
        parties = [(g.party_id, g.category, g.value) for g in self.party_scopes]
        if len(own) != len(set(own)) or len(parties) != len(set(parties)):
            raise ValueError("同一对象的范围类别不能重复")
        if self.enabled and not self.own_scopes:
            raise ValueError("启用前须配置本企业范围")
        return self


class AdmissionSave(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    payload: AdmissionPayload
    reason: Text


class AdmissionReview(DTO):
    expected_version: Version
    reason: Text


class AdmissionPreview(DTO):
    party_id: UUID
    sku_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    source: Literal["ACTIVE", "DRAFT"] = "ACTIVE"
