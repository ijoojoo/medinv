from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr, StringConstraints, model_validator

from medinv.modules.catalog.api.schemas import DTO, Text, Version

CodeOwner = Literal["skus", "parties"]
CodeScheme = Literal[
    "BARCODE",
    "UDI_DI",
    "NHSA_DRUG",
    "NHSA_CONSUMABLE",
    "NHSA_IVD",
    "NHSA_ORGANIZATION",
    "MSFX",
    "MEITUAN",
    "ELEME",
    "TAOBAO",
    "WANGDIANTONG",
    "CUSTOM",
]
STANDARD_SCHEMES = {
    "BARCODE",
    "UDI_DI",
    "NHSA_DRUG",
    "NHSA_CONSUMABLE",
    "NHSA_IVD",
    "NHSA_ORGANIZATION",
}


class CatalogCodeCreate(DTO):
    scheme: CodeScheme
    namespace: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    external_code: Annotated[
        StrictStr,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f]+$"
        ),
    ]
    packaging_id: UUID | None = None
    evidence_ref: Annotated[Text, Field(max_length=120)]
    reason: Text

    @model_validator(mode="after")
    def namespace_rules(self) -> CatalogCodeCreate:
        if self.scheme in STANDARD_SCHEMES and self.namespace:
            raise ValueError("标准编码不填写平台账号范围")
        if self.scheme not in STANDARD_SCHEMES and not self.namespace:
            raise ValueError("平台编码须填写店铺、账号或系统范围")
        return self


class CatalogCodeState(DTO):
    expected_version: Version
    state: Literal["ACTIVE", "INACTIVE"]
    reason: Text
