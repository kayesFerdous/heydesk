"""The `WhatsAppApi` port: the few Cloud API calls HeyDesk makes for WhatsApp.

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

    async def token_from_code(self, app_id: str, app_secret: str, code: str) -> str:
        """The business's token for the code Embedded Signup gave the browser. A code
        works once."""
        ...

    async def register(self, token: str, phone_id: str, pin: str) -> None:
        """Puts the number on the Cloud API, with `pin` as its two-step PIN."""
        ...

    async def send_message(
        self, token: str, phone_id: str, to: str, text: str, choices: list[str] | None = None
    ) -> None:
        """`to` is the visitor's WhatsApp number. `choices` are reply buttons, or a list past
        three: a tap comes back as a message with the choice as its id."""
        ...

    async def send_cards(
        self, token: str, phone_id: str, to: str, cards: list[dict[str, str]]
    ) -> list[str]:
        """Products, one message each: `{name, price, url}`, the photo's full URL, and maybe
        `choice`: an Order button whose tap comes back with it. Its `page` and `video`
        are links in the caption. Returns each card's message id, in order,
        so a reply to one is known."""
        ...

    async def send_link(
        self, token: str, phone_id: str, to: str, text: str, title: str, url: str
    ) -> None:
        """Text with one button that opens `url`: a receipt's Pay button."""
        ...

    async def send_list(
        self, token: str, phone_id: str, to: str, text: str, rows: list[dict[str, str]]
    ) -> None:
        """A list under the text: up to 10 rows `{id, title, description?}` (a title is 24
        characters, a description 72). A tapped row comes back with its id."""
        ...

    async def download(self, token: str, media_id: str) -> bytes:
        """A photo the visitor sent, by the media id in its message."""
        ...

    async def send_typing(self, token: str, phone_id: str, message_id: str) -> None:
        """Marks their message read and shows "typing…", until the reply or 25 seconds."""
        ...
