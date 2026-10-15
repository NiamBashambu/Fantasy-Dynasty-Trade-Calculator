"""Small thread-safe in-memory helpers (no external storage)."""

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple


class TTLCache:
    """Bounded cache whose entries expire after ``ttl`` seconds."""

    def __init__(self, ttl: float, max_size: int = 256, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.max_size = max_size
        self._clock = clock
        self._data: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._clock() >= expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            now = self._clock()
            if len(self._data) >= self.max_size and key not in self._data:
                # Drop expired entries first, then the entry closest to expiry.
                for k in [k for k, (exp, _) in self._data.items() if exp <= now]:
                    del self._data[k]
                if len(self._data) >= self.max_size:
                    oldest = min(self._data, key=lambda k: self._data[k][0])
                    del self._data[oldest]
            self._data[key] = (now + self.ttl, value)


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per ``window`` seconds for each key.

    State is per process and resets on restart. It exists to protect upstream
    quotas (OpenAI spend, Sleeper), not to enforce hard guarantees.
    """

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return False
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10000:
                self._prune(cutoff)
            return True

    def _prune(self, cutoff: float) -> None:
        for k in [k for k, ev in self._events.items() if not ev or ev[-1] <= cutoff]:
            del self._events[k]
