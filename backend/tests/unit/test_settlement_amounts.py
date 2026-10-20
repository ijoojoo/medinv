from uuid import uuid4

import pytest
from pydantic import ValidationError

from medinv.modules.settlement.api.schemas import CashCreate


@pytest.mark.parametrize("amount", [0, 1.2, "0", "-1", "1.001", "1e2", "1000000000000000000"])
def test_cash_amount_boundary_is_exact(amount):
    with pytest.raises(ValidationError):
        CashCreate(
            client_operation_id=uuid4(),
            entity_id=uuid4(),
            party_id=uuid4(),
            account_id=uuid4(),
            direction="RECEIPT",
            amount=amount,
            occurred_on="2026-10-10",
            reference="TEST",
            note="TEST",
            confirmed=True,
        )
