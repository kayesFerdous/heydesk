"""Email to an owner: the codes that verify their email and reset their password."""

from typing import Protocol


class Mailer(Protocol):
    async def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        """Sends one email: `text`, and `html` for the apps that show it. A failure is logged,
        not raised: the owner asks again."""
        ...
