"""Rate limits (D52): how fast someone can send chat messages or try to log in.

The daily limits (D51) stop slow timepass; these stop bursts, such as a script sending many
messages a second or making a new visitor id for every message. Too many → a 429.
The counts are kept in this server's memory.
"""

import logging

from limits import parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import SlidingWindowCounterRateLimiter

from heydesk.errors import TooFast

log = logging.getLogger(__name__)

CHAT_PER_VISITOR = "6/minute"
CHAT_PER_IP = "60/minute"  # generous: on mobile data many people share one address
LOGIN = "10/15 minutes"  # per address and email: stops password guessing


class RateLimiter:
    def __init__(self) -> None:
        self.limiter = SlidingWindowCounterRateLimiter(MemoryStorage())
        self.chat_per_ip = CHAT_PER_IP  # a test can lower it

    async def chat(self, visitor: str, ip: str) -> None:
        await self._check(CHAT_PER_VISITOR, "chat-visitor", visitor)
        await self._check(self.chat_per_ip, "chat-ip", ip)

    async def login(self, ip: str, email: str) -> None:
        await self._check(LOGIN, "login", ip, email.strip().lower())

    async def _check(self, limit: str, what: str, *key: str) -> None:
        """Counts one hit; raises `TooFast` when there were too many."""
        if not await self.limiter.hit(parse(limit), what, *key):
            log.info("rate limited", extra={"what": what})
            raise TooFast("Too many requests. Please wait a minute and try again.")
