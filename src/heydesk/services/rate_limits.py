"""Rate limits: how fast someone can send chat messages, log in or sign up.

The daily limits stop slow timepass; these stop bursts, such as a script sending many
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
SIGNUP = "5/hour"  # per address: stops a script making businesses
SEND_CODE = "3/hour"  # emails with a code, per person: stops filling someone's inbox
SEND_CODE_PER_IP = "10/hour"  # and per address: stops spending our free 300 emails a day
CHECK_CODE = "5/15 minutes"  # per person: a 6-digit code can't be guessed in 5 tries


class RateLimiter:
    def __init__(self) -> None:
        self.limiter = SlidingWindowCounterRateLimiter(MemoryStorage())
        self.chat_per_ip = CHAT_PER_IP  # a test can lower it

    async def chat(self, visitor: str, ip: str) -> None:
        await self._check(CHAT_PER_VISITOR, "chat-visitor", visitor)
        await self._check(self.chat_per_ip, "chat-ip", ip)

    async def login(self, ip: str, email: str) -> None:
        await self._check(LOGIN, "login", ip, email.strip().lower())

    async def signup(self, ip: str) -> None:
        await self._check(SIGNUP, "signup", ip)

    async def send_code(self, ip: str, who: str) -> None:
        """`who` is the email, or the user id when they're logged in."""
        await self._check(SEND_CODE, "send-code", who.strip().lower())
        await self._check(SEND_CODE_PER_IP, "send-code-ip", ip)

    async def check_code(self, who: str) -> None:
        await self._check(CHECK_CODE, "check-code", who.strip().lower())

    async def _check(self, limit: str, what: str, *key: str) -> None:
        """Counts one hit; raises `TooFast` when there were too many."""
        if not await self.limiter.hit(parse(limit), what, *key):
            log.info("rate limited", extra={"what": what})
            raise TooFast("Too many requests. Please wait a minute and try again.")
