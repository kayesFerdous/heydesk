"""The `TelegramApi` port: the few Bot API calls HeyDesk makes (D42).

Every call takes the bot's token: each business connects its own bot, so there's no one
token for the platform.
"""

from typing import Protocol


class TelegramUnavailable(Exception):
    """Telegram said no (a wrong token) or couldn't be reached. Never carries the token."""


class TelegramApi(Protocol):
    async def get_me(self, token: str) -> str:
        """The bot's username, without the @. Fails for a wrong token."""
        ...

    async def set_webhook(
        self, token: str, url: str, secret: str, drop_pending: bool = True
    ) -> None:
        """Telegram posts each new message to `url`, with `secret` in a header."""
        ...

    async def delete_webhook(self, token: str) -> None: ...

    async def send_message(
        self, token: str, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        """`choices` are buttons under the text (D99): a tap comes back as a callback query
        with the choice as its data."""
        ...

    async def send_card(
        self, token: str, chat_id: str, html: str, buttons: list[dict[str, str]]
    ) -> None:
        """A card as HTML text (D111), with a button a row under it: `{title, choice}` sends
        the choice back like a choice's tap (D99); `{title, url}` opens the link (a Pay button)."""
        ...

    async def send_photo(
        self,
        token: str,
        chat_id: str,
        jpeg: bytes,
        caption: str,
        choice: str = "",
        page: str = "",
        video: str = "",
    ) -> None:
        """A photo uploaded from us, so it needs no public URL (D63); with `choice`, an Order
        button under it whose tap sends it back (D99); with `page`, a Details button that opens
        the product's page (D121); with `video`, a Video button that opens it (D125)."""
        ...

    async def answer_callback(self, token: str, callback_id: str) -> None:
        """Tells Telegram a button's tap was received, so its spinner stops."""
        ...

    async def download(self, token: str, file_id: str) -> bytes:
        """A photo the visitor sent, by the `file_id` in its message (bots get up to 20 MB)."""
        ...

    async def send_typing(self, token: str, chat_id: str) -> None:
        """Shows "typing…" in the chat for about five seconds."""
        ...
