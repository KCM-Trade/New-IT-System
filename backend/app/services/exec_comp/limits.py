"""Server-wide query slots and the per-query time budget (01 D21).

Prod runs ``uvicorn --workers 4``: a ``threading.Semaphore`` would cap each
worker separately (4 x N). The cap here is shared by every process on the
host through N lock files under ``EXEC_COMP_SLOT_DIR``: holding slot i =
holding ``flock(LOCK_EX)`` on ``slot-i.lock``. The kernel releases a flock
when its descriptor closes, so a crashed or killed worker can never leak a
slot. Separate ``open()`` calls get separate locks on Linux, so threads of
one process contend correctly too.
"""

from __future__ import annotations

import fcntl
import os
import time
from typing import Optional

from .errors import ExecCompError


class QuerySlot:
    """Context manager: take one of ``max_concurrent`` slots or raise BUSY."""

    def __init__(self, slot_dir: str, max_concurrent: int) -> None:
        self._dir = slot_dir
        self._n = max(1, int(max_concurrent))
        self._fd: Optional[int] = None

    def __enter__(self) -> "QuerySlot":
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        for i in range(self._n):
            fd = os.open(os.path.join(self._dir, f"slot-{i}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            self._fd = fd
            return self
        raise ExecCompError(
            "BUSY",
            f"all {self._n} exec-compensation query slots are in use; retry in a minute",
            status=503,
        )

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class Deadline:
    """Wall-clock budget for one query run; checked between replica batches."""

    def __init__(self, budget_s: float) -> None:
        self.budget_s = float(budget_s)
        self._end = time.monotonic() + self.budget_s

    def remaining(self) -> float:
        return self._end - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, stage: str = "") -> None:
        if self.expired():
            where = f" ({stage})" if stage else ""
            raise ExecCompError(
                "QUERY_TOO_LARGE",
                f"query exceeded its {self.budget_s:g}s time budget{where}; "
                "narrow the date range",
            )
