from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from medinv.platform.errors import BusinessError


def document_filters(
    alias: str,
    searchable: str,
    states: dict[str, str],
    search: str = "",
    state: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    *,
    date_column: str = "created_at",
) -> tuple[str, dict[str, Any]]:
    """SQL expressions come from code; all user input is bound as values."""
    if len(search) > 100 or (state and state not in states):
        raise BusinessError("INVALID_QUERY", "查询文字过长或单据状态无效", 422)
    if date_to == date.max or (date_from and date_to and date_from > date_to):
        raise BusinessError("INVALID_QUERY", "日期范围无效，请检查起止日期", 422)
    clauses = []
    params: dict[str, Any] = {}
    if search.strip():
        clauses.append(f"strpos(lower({searchable}),lower(:document_search))>0")
        params["document_search"] = search.strip()
    if state:
        clauses.append(states[state])
    zone = ZoneInfo("Asia/Shanghai")
    if date_from:
        clauses.append(f"{alias}.{date_column}>=:document_from")
        params["document_from"] = datetime.combine(date_from, time.min, zone)
    if date_to:
        clauses.append(f"{alias}.{date_column}<:document_until")
        params["document_until"] = datetime.combine(date_to + timedelta(days=1), time.min, zone)
    return "".join(f" AND ({clause})" for clause in clauses), params
