"""Attempt rate limiting (gap P1-18).

Submitting an attempt triggers real validator work — read-only warehouse SQL
and/or workspace SDK calls — so an attacker or a stuck client retry loop can
drive unbounded warehouse cost and queue depth. This adds a per-player (and
per-team) sliding-window limit on attempt submissions.

The limiter is a pure, process-local sliding window with an injectable clock so
it is fully unit-testable. Process-local is acceptable for a first cut: in the
federated topology each child instance is one workspace (a handful of players),
and the master is one process; a shared/distributed limiter is a later upgrade.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Allow at most ``max_events`` per ``window_seconds`` per key."""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record an attempt for ``key``; return True if within the limit.

        ``max_events <= 0`` disables limiting (always allowed). Expired
        timestamps are evicted on each call so memory stays bounded.
        """
        if self.max_events <= 0:
            return True
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            q = self._events[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> float:
        """Seconds until the oldest in-window event for ``key`` expires."""
        with self._lock:
            q = self._events.get(key)
            if not q:
                return 0.0
            return max(0.0, self.window_seconds - (self._clock() - q[0]))


__all__ = ["SlidingWindowLimiter"]
