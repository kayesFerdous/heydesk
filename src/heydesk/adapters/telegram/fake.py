"""FakeTelegram: remembers what would have gone to Telegram, for tests. The token "bad" fails."""

from heydesk.ports.telegram import TelegramUnavailable


class FakeTelegram:
    def __init__(self) -> None:
        self.webhooks: dict[str, tuple[str, str]] = {}  # token -> (url, secret)
        self.sent: list[tuple[str, str]] = []  # (chat_id, text), in order

    async def get_me(self, token: str) -> str:
        if token == "bad":
            raise TelegramUnavailable("getMe: Unauthorized")
        return "spice_garden_bot"

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        self.webhooks[token] = (url, secret)

    async def delete_webhook(self, token: str) -> None:
        self.webhooks.pop(token, None)

    async def send_message(self, token: str, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    async def send_typing(self, token: str, chat_id: str) -> None:
        pass
