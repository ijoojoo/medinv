"""Dictionary wording never changes decisions, quantity checks or reviewer permissions."""

from typing import Any

from medinv.modules.catalog.application.dictionaries import (
    PURCHASE_DICTIONARIES,
    current_dictionary,
    dictionary_lock,
    validate_dictionary,
)
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def options(uow: TenantUnitOfWork, ctx: TenantContext) -> dict[str, Any]:
    ctx.require("purchase.read")
    with uow.open(ctx, readonly=True) as session:
        repo = SqlSession(session)
        dictionary_lock(repo, ctx)
        return {"items": [current_dictionary(repo, key) for key in PURCHASE_DICTIONARIES]}


def record_note(
    repo: SqlSession,
    ctx: TenantContext,
    key: str,
    conclusion: str | None,
    note: str,
    capacity: int,
) -> str:
    if conclusion is None:
        return note  # Existing integrations submit their original written evidence.
    validate_dictionary(repo, ctx, key, conclusion)
    if key in {"inspection_mixed", "inspection_rejected"} and not note:
        raise BusinessError(
            "INSPECTION_REASON_REQUIRED", "存在拒收数量，请填写验收依据及拒收原因", 422
        )
    result = conclusion + (f"；{note}" if note else "")
    if len(result) > capacity:
        raise BusinessError("CONCLUSION_TOO_LONG", f"结论与补充说明合计不能超过 {capacity} 字", 422)
    return result
