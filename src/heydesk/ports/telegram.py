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

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        """Telegram posts each new message to `url`, with `secret` in a header."""
        ...

    async def delete_webhook(self, token: str) -> None: ...

    async def send_message(self, token: str, chat_id: str, text: str) -> None: ...

    async def send_typing(self, token: str, chat_id: str) -> None:
        """Shows "typing…" in the chat for about five seconds."""
        ...
