"""What one address may try, counted in this process (the One Occupella plan of 11/05, Phase 1,
item 2).

A login already locks for 15 minutes after ten wrong passwords (accounts.MAX_FAILURES). That stops
guessing one person's password, but it doesn't slow someone trying a few passwords on many
people's emails, and it lets anyone lock out a person whose email they know. So:

- an address that failed SIGN_IN_FAILURES sign-ins within SIGN_IN_WINDOW is refused (429, with
  Retry-After) until its oldest failure leaves the window, whatever email it tries next;
- an address asks for at most RESETS password resets within RESET_WINDOW, so nobody fills
  strangers' inboxes through the forgot-password page.

The address is the connection's, or behind a proxy the app trusts (PMS_TRUSTED_PROXIES) the
visitor's as the proxy names it; an IPv6 one counts as its whole /64. Counted per process, like an
API key's rate (ai_keys.PER_MINUTE): a server running several processes allows that many times as
much, still a small number. Each app holds its own counts (create_app), so tests don't share them.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request

SIGN_IN_FAILURES = 20
SIGN_IN_WINDOW = 15 * 60.0
RESETS = 10
RESET_WINDOW = 60 * 60.0
# The addresses a limiter remembers at most; the one heard from longest ago goes first.
REMEMBERED = 50_000


class Limiter:
    """At most `limit` events from one address within `window` seconds."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def wait(self, address: str) -> float | None:
        """Seconds until this address may try again, or None if it may now."""
        now = self._clock()
        with self._lock:
            times = self._recent(address, now)
            if times is None or len(times) < self.limit:
                return None
            return times[0] + self.window - now

    def hit(self, address: str) -> None:
        """Count one event from this address."""
        now = self._clock()
        with self._lock:
            times = self._recent(address, now)
            if times is None:
                times = self._seen[address] = deque()
            times.append(now)
            self._seen.move_to_end(address)
            while len(self._seen) > REMEMBERED:
                self._seen.popitem(last=False)

    def _recent(self, address: str, now: float) -> deque[float] | None:
        times = self._seen.get(address)
        if times is None:
            return None
        while times and times[0] <= now - self.window:
            times.popleft()
        if not times:
            del self._seen[address]
            return None
        return times


def counted_as(host: str) -> str:
    """The address a limit counts a host as: an IPv6 address as its /64, which one visitor holds
    whole and may use any address of (docs/edge-cases.md U-032); an IPv4 address, written as
    IPv6 or not, as itself; anything else as it came."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)


def address_of(request: Request) -> str:
    """The address a request came from (behind a proxy the app trusts, the visitor's), as a limit
    counts it (counted_as)."""
    return counted_as(request.client.host) if request.client is not None else "unknown"


def refuse_if_waiting(limiter: Limiter, address: str, what: str) -> None:
    """429, with Retry-After, while this address must wait."""
    wait = limiter.wait(address)
    if wait is None:
        return
    seconds = max(1, math.ceil(wait))
    minutes = math.ceil(seconds / 60)
    raise HTTPException(
        429,
        f"too many {what} from this address: try again in {minutes}"
        f" minute{'' if minutes == 1 else 's'}",
        headers={"Retry-After": str(seconds)},
    )
