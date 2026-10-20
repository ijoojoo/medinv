from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints

Short = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=100)]
Address = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=300)]
Reference = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=120)]


class PartyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    short_name: Short = ""
    search_alias: Short = ""
    organization_type: Literal[
        "UNKNOWN", "MANUFACTURER", "WHOLESALER", "RETAILER", "MEDICAL_INSTITUTION", "OTHER"
    ] = "UNKNOWN"
    contact_name: Short = ""
    contact_phone: Annotated[
        StrictStr,
        StringConstraints(
            strip_whitespace=True, max_length=50, pattern=r"^[0-9+()（） .转分机-]*$"
        ),
    ] = ""
    legal_representative: Short = ""
    registered_address: Address = ""
    business_address: Address = ""
    delivery_address: Address = ""
    business_scope_text: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, max_length=1000)
    ] = ""
    qualification_archive_ref: Reference = ""
    authorization_archive_ref: Reference = ""
    quality_agreement_ref: Reference = ""


PARTY_PROFILE_FIELDS = tuple(PartyProfile.model_fields)
