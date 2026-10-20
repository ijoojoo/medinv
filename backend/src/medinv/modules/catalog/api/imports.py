from typing import Annotated

from pydantic import StrictStr, StringConstraints

from medinv.modules.catalog.api.schemas import DTO


class ProductImport(DTO):
    content: Annotated[StrictStr, StringConstraints(min_length=1, max_length=262144)]
