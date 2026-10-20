from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class SqlSession:
    """Transaction-bound SQL adapter; never commits or opens its own connection."""

    def __init__(self, session: Session):
        self.session = session

    def rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.session.execute(text(sql), params).mappings()]

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.session.execute(text(sql), params)
