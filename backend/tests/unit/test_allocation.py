from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from medinv.modules.inventory.domain.allocation import Candidate, allocate_fefo


def test_fefo_uses_earliest_expiry_and_does_not_mutate_candidates():
    early, late, undated = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    candidates = [
        Candidate(late, now + timedelta(days=2), Decimal(10)),
        Candidate(undated, None, Decimal(10)),
        Candidate(early, now, Decimal(2)),
    ]
    assert allocate_fefo(candidates, Decimal(3)) == [(early, Decimal(2)), (late, Decimal(1))]
    assert candidates[2].available == 2
    with pytest.raises(ValueError):
        allocate_fefo(candidates, Decimal(23))


def test_fefo_compares_instants_across_time_zones():
    early, late = uuid4(), uuid4()
    candidates = [
        Candidate(late, datetime.fromisoformat("2027-01-01T09:00:00+00:00"), Decimal(1)),
        Candidate(early, datetime.fromisoformat("2027-01-01T12:00:00+08:00"), Decimal(1)),
    ]
    assert allocate_fefo(candidates, Decimal(1)) == [(early, Decimal(1))]
