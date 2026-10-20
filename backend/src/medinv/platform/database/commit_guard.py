from collections.abc import Callable
from contextvars import ContextVar

# API commands keep the control-plane authorization lock through business commit.
# Probe immediately before commit so a lost/expired control connection causes rollback.
before_business_commit: ContextVar[Callable[[], None] | None] = ContextVar(
    "before_business_commit", default=None
)
