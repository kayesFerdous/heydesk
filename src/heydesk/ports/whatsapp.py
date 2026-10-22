"""The `WhatsAppApi` port: the few Cloud API calls HeyDesk makes for WhatsApp (D103).

Every call takes the business's access token and its number's id (`phone_id`): each business
connects its own number to our Meta app.
"""

from typing import Protocol


class WhatsAppUnavailable(Exception):
    """Meta said no (a wrong token, the 24-hour window closed) or couldn't be reached.
    Never carries the token."""


class WhatsAppApi(Protocol):
    async def subscribe(self, token: str, account_id: str, phone_id: str) -> None:
        """Meta posts the business account's new messages to our app's webhook. Fails when
        the token isn't for that account and number, so it is also the check on them."""
        ...

    async def send_message(
        self, token: str, phone_id: str, to: str, text: str, choices: list[str] | None = None
    ) -> None:
        """`to` is the visitor's WhatsApp number. `choices` are reply buttons, or a list past
        three (D99): a tap comes back as a message with the choice as its id."""
        ...

    async def send_cards(
        self, token: str, phone_id: str, to: str, cards: list[dict[str, str]]
    ) -> None:
        """Products, one message each: `{name, price, url}`, the photo's full URL, and maybe
        `choice`: an Order button whose tap comes back with it (D99)."""
        ...

    async def download(self, token: str, media_id: str) -> bytes:
        """A photo the visitor sent, by the media id in its message."""
        ...

    async def send_typing(self, token: str, phone_id: str, message_id: str) -> None:
        """Marks their message read and shows "typing…", until the reply or 25 seconds."""
        ...
