"""Stable business errors of the exec-compensation API (03 §4.2 rule 8).

Raised anywhere below the route; ``main.py`` renders them as
``{"error": {"code", "message"}}`` with ``status``. Codes are part of the
public contract (``schemas.exec_compensation.ErrorCode``).
"""

from __future__ import annotations


class ExecCompError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
