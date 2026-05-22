from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """
    In-memory sliding-window rate limiter. Thread-safe.

    Key format: "{endpoint}:{client_ip}" — each caller/endpoint pair is tracked
    independently so login and signup limits do not share a bucket.

    Production note: this limiter is per-process. For multi-replica deployments
    (ECS Fargate, K8s) move rate-limiting upstream:
      - Redis with a sliding-window Lua script (e.g. redis-py with EVALSHA)
      - AWS API Gateway usage plans
      - AWS WAF rate-based rules

    Memory: O(limit_per_minute * unique_keys). Stale entries are pruned on each
    check() call — no background GC thread required.
    """

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._window = 60.0  # seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Return True (allowed) or False (rate limit exceeded)."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True
