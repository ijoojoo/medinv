from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Candidate:
    balance_id: UUID
    expires_at: datetime | None
    available: Decimal


def allocate_fefo(candidates: list[Candidate], requested: Decimal) -> list[tuple[UUID, Decimal]]:
    """All candidates must already be eligible and locked by the caller."""
    if not requested.is_finite() or requested <= 0:
        raise ValueError("positive finite quantity required")
    ordered = sorted(
        candidates,
        key=lambda c: (
            c.expires_at is None,
            c.expires_at or datetime.max.replace(tzinfo=UTC),
            str(c.balance_id),
        ),
    )
    remaining = requested
    result = []
    for candidate in ordered:
        taken = min(remaining, max(candidate.available, Decimal(0)))
        if taken:
            result.append((candidate.balance_id, taken))
            remaining -= taken
        if remaining == 0:
            return result
    raise ValueError("insufficient eligible stock")
