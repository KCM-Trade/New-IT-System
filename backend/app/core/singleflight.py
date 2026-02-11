"""
SingleFlight: coalesce concurrent identical requests into one execution.

When multiple threads call do(key, fn) with the same key concurrently,
only the first caller actually executes fn(). All other callers block
and receive the same result (or exception) once the first caller finishes.

This is especially useful for expensive ClickHouse queries -- if 4 identical
requests arrive before the Redis cache is populated, only 1 hits ClickHouse.

Inspired by Go's singleflight package.
"""

import threading
from typing import Any, Callable, Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class _Call:
    """Represents a single in-flight function execution."""

    def __init__(self):
        # Event signals when the execution is done
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[Exception] = None


class SingleFlight:
    """
    Thread-safe request coalescing utility.

    Usage:
        sf = SingleFlight()
        result = sf.do("cache_key_abc", lambda: expensive_query())
    """

    def __init__(self):
        self._mu = threading.Lock()
        self._calls: dict = {}

    def do(self, key: str, fn: Callable[[], Any]) -> Any:
        """
        Execute fn() for the given key, or wait for an existing execution.

        Args:
            key: Unique identifier for the request (e.g. Redis cache key).
            fn:  Zero-argument callable that performs the actual work.

        Returns:
            The result of fn().

        Raises:
            Whatever exception fn() raises (propagated to all waiters).
        """
        with self._mu:
            if key in self._calls:
                # Another thread is already executing this key -- just wait
                call = self._calls[key]
                is_owner = False
                logger.info(f"SingleFlight: coalescing request for key={key[:50]}...")
            else:
                # First caller for this key -- register and execute
                call = _Call()
                self._calls[key] = call
                is_owner = True

        if is_owner:
            # Execute the function and broadcast the result
            try:
                call.result = fn()
            except Exception as e:
                call.error = e
            finally:
                call.event.set()
                with self._mu:
                    self._calls.pop(key, None)
        else:
            # Wait for the owner thread to finish
            call.event.wait()

        # Propagate error to all callers (owner and waiters alike)
        if call.error is not None:
            raise call.error

        return call.result
