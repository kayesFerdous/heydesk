"""How fast anyone can call the public routes and log in (D52).

The daily limits (D51, `visitors.py`) stop slow, steady timepass; these stop bursts: a script
sending many messages a second, or making a new visitor id for every message. Each check counts
one hit over a sliding window and raises `TooFast` (a 429) past the limit. The limits are
settings (`RATE_*`); the counts live in `RATE_LIMIT_STORAGE`.
"""

import logging

from limits import parse
from limits.aio.strategies import SlidingWindowCounterRateLimiter
from limits.storage import storage_from_string

from heydesk.config import Settings
from heydesk.errors import TooFast

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter = SlidingWindowCounterRateLimiter(
            storage_from_string(settings.RATE_LIMIT_STORAGE)
        )

    async def chat(self, visitor: str, ip: str) -> None:
        """A web chat message: per visitor, and per address for a script that keeps making
        new visitor ids."""
        await self._check(self.settings.RATE_CHAT_PER_VISITOR, "chat-visitor", visitor)
        await self._check(self.settings.RATE_CHAT_PER_IP, "chat-ip", ip)

    async def telegram(self, channel_id: str, chat_id: str) -> None:
        await self._check(self.settings.RATE_CHAT_PER_VISITOR, "telegram-chat", channel_id, chat_id)

    async def call(self, ip: str) -> None:
        await self._check(self.settings.RATE_CALL_PER_IP, "call-ip", ip)

    async def poll(self, conversation_id: str) -> None:
        """The chat page asking for team replies. By conversation, not address: many people
        share one."""
        await self._check(self.settings.RATE_POLL_PER_CONVERSATION, "poll", conversation_id)

    async def login(self, ip: str, email: str) -> None:
        await self._check(self.settings.RATE_LOGIN, "login", ip, email.strip().lower())

    async def signup(self, ip: str) -> None:
        await self._check(self.settings.RATE_SIGNUP_PER_IP, "signup-ip", ip)

    async def _check(self, limit: str, what: str, *key: str) -> None:
        if not await self.limiter.hit(parse(limit), what, *key):
            log.info("rate limited", extra={"what": what, "limit": limit})  # no key: it's an id
            raise TooFast("Too many requests. Please wait a minute and try again.")
