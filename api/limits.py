"""Two brakes for an API that strangers can reach: a rate limit and a daily model budget.

The rate limit counts requests per caller in a sliding window. The budget adds up what the
model calls cost today and refuses new runs once the cap is reached. Both live in memory, so a
restart forgets them. That is fine for a demo and it is not the last line of defence: the real
limit belongs on the provider's key itself.
"""

import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable

MAX_CALLERS = 10_000


class RateLimiter:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def wait_for(self, key: str) -> float:
        """Counts this request. Returns 0 when it may go ahead, otherwise the seconds to wait."""
        now = self.clock()
        with self.lock:
            if len(self.hits) > MAX_CALLERS:
                self._forget_quiet_callers(now)
            hits = self.hits[key]
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window_s - (now - hits[0])
            hits.append(now)
            return 0.0

    def _forget_quiet_callers(self, now: float) -> None:
        """Drops everyone whose last request has left the window, so the table cannot grow for ever."""
        for key in [k for k, hits in self.hits.items() if not hits or now - hits[-1] >= self.window_s]:
            del self.hits[key]


class Budget:
    """What the model calls have cost today, in rupees, against an optional cap."""

    def __init__(self, cap_inr: float | None, today: Callable[[], str] | None = None):
        self.cap_inr = cap_inr
        self.today = today or (lambda: datetime.now(timezone.utc).date().isoformat())
        self.day, self.spent_inr = self.today(), 0.0
        self.lock = threading.Lock()

    def _roll(self) -> None:
        if self.today() != self.day:
            self.day, self.spent_inr = self.today(), 0.0

    def add(self, cost_inr: float) -> None:
        with self.lock:
            self._roll()
            self.spent_inr += max(0.0, cost_inr)

    def spent(self) -> float:
        with self.lock:
            self._roll()
            return round(self.spent_inr, 4)

    def used_up(self) -> bool:
        return self.cap_inr is not None and self.spent() >= self.cap_inr


def number(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    return float(value) if value else default


def demo_mode() -> bool:
    return (os.getenv("DEFLECT_DEMO") or "").lower() in ("1", "true", "yes")


def write_limiter() -> RateLimiter:
    """How many runs one caller may start in a minute. A public demo is stricter by default."""
    return RateLimiter(int(number("DEFLECT_RATE_PER_MINUTE", 6 if demo_mode() else 60)), 60)


def budget() -> Budget:
    return Budget(number("DEFLECT_DAILY_BUDGET_INR", 25.0 if demo_mode() else None))
