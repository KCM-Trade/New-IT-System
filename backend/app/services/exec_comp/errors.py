"""Stable business errors of the exec-compensation API (03 §4.2 rule 8).

Raised anywhere below the route; ``main.py`` renders them as
``{"error": {"code", "message"}}`` with ``status``. Codes are part of the
public contract (``schemas.exec_compensation.ErrorCode``); the HTTP status of
each code is ``schemas.exec_compensation.ERROR_STATUS`` unless given.
"""

from __future__ import annotations

from typing import Optional

from app.schemas.exec_compensation import ERROR_STATUS


class ExecCompError(Exception):
    def __init__(self, code: str, message: str, status: Optional[int] = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status if status is not None else ERROR_STATUS.get(code, 422)
