"""TelegramBot: Telegram's Bot API behind the `TelegramApi` port (D42), over httpx.

The token is part of every URL, so httpx's own request log is off (`logs.py`) and an error
never repeats the URL.
"""

from typing import Any

import httpx

from heydesk.ports.telegram import TelegramUnavailable

API = "https://api.telegram.org"


class TelegramBot:
    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def get_me(self, token: str) -> str:
        me = await self._call(token, "getMe")
        return str(me["username"])

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        await self._call(
            token,
            "setWebhook",
            url=url,
            secret_token=secret,
            allowed_updates=["message"],  # not edits, reactions or group events
            drop_pending_updates=True,  # what was sent before it was connected isn't answered
        )

    async def delete_webhook(self, token: str) -> None:
        await self._call(token, "deleteWebhook")

    async def send_message(self, token: str, chat_id: str, text: str) -> None:
        await self._call(token, "sendMessage", chat_id=chat_id, text=text)

    async def send_typing(self, token: str, chat_id: str) -> None:
        await self._call(token, "sendChatAction", chat_id=chat_id, action="typing")

    async def _call(self, token: str, method: str, **params: Any) -> Any:
        try:
            res = await self.client.post(f"{API}/bot{token}/{method}", json=params)
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramUnavailable(f"{method}: Telegram didn't answer") from None
        if not body.get("ok"):
            raise TelegramUnavailable(f"{method}: {body.get('description', res.status_code)}")
        return body["result"]
