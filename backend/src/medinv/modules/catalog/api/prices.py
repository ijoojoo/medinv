from typing import Annotated

from pydantic import Field, StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO, Text

Price = Annotated[StrictStr, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")]


class ReferencePricesUpdate(DTO):
    expected_version: Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
    purchase_price: Price | None = None
    wholesale_price: Price | None = None
    retail_price: Price | None = None
    note: Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    reason: Text
