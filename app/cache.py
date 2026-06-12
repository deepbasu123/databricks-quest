"""Tiny thread-safe TTL cache for read-heavy, poll-driven endpoints (P2-4).

The leaderboard is recomputed against the DB on every client poll; with many
attendees polling, that's needless repeated aggregation. A short TTL collapses a
burst of polls into one query while keeping the view fresh within a few seconds —
the right trade-off for a leaderboard (eventual freshness, not transactional).

Deliberately minimal: in-process, monotonic-clock TTL, no eviction policy beyond
expiry (the key space is tiny — a handful of leaderboard periods). A TTL of 0
disables caching so callers can turn it off without branching.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        """Return the cached value for ``key`` if still fresh, else ``None``."""
        if self.ttl <= 0:
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if (time.monotonic() - ts) >= self.ttl:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._store[key] = (time.monotonic(), value)

    def get_or_compute(self, key: Any, producer: Callable[[], Any]) -> Any:
        """Return the cached value, or compute + store it on a miss."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        self.set(key, value)
        return value

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
