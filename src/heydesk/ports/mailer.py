"""Email to an owner: the codes that verify their email and reset their password (D93)."""

from typing import Protocol


class Mailer(Protocol):
    async def send(self, to: str, subject: str, text: str) -> None:
        """Sends one plain-text email. A failure is logged, not raised: the owner asks again."""
        ...
