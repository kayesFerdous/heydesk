"""FakeTelegram: remembers what would have gone to Telegram, for tests. The token "bad" fails."""

from heydesk.ports.telegram import TelegramUnavailable


class FakeTelegram:
    def __init__(self) -> None:
        self.webhooks: dict[str, tuple[str, str]] = {}  # token -> (url, secret)
        self.sent: list[tuple[str, str]] = []  # (chat_id, text), in order
        self.photos: list[tuple[str, str]] = []  # (chat_id, caption) of each photo sent
        self.pages: list[tuple[str, str]] = []  # (chat_id, url) of each photo's Details button
        self.choices: list[tuple[str, list[str]]] = []  # (chat_id, buttons) of a message or photo
        self.cards: list[tuple[str, str, list[dict[str, str]]]] = []  # (chat_id, html, buttons)
        self.answered: list[str] = []  # the callback queries answered
        self.files: dict[str, bytes] = {}  # what a visitor's photo's file_id downloads as

    async def get_me(self, token: str) -> str:
        if token == "bad":
            raise TelegramUnavailable("getMe: Unauthorized")
        return "spice_garden_bot"

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        self.webhooks[token] = (url, secret)

    async def delete_webhook(self, token: str) -> None:
        self.webhooks.pop(token, None)

    async def send_message(
        self, token: str, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        self.sent.append((chat_id, text))
        if choices:
            self.choices.append((chat_id, choices))

    async def send_card(
        self, token: str, chat_id: str, html: str, buttons: list[dict[str, str]]
    ) -> None:
        self.cards.append((chat_id, html, buttons))

    async def send_photo(
        self, token: str, chat_id: str, jpeg: bytes, caption: str, choice: str = "", page: str = ""
    ) -> None:
        self.photos.append((chat_id, caption))
        if choice:
            self.choices.append((chat_id, [choice]))
        if page:
            self.pages.append((chat_id, page))

    async def answer_callback(self, token: str, callback_id: str) -> None:
        self.answered.append(callback_id)

    async def download(self, token: str, file_id: str) -> bytes:
        if file_id not in self.files:
            raise TelegramUnavailable("getFile: Bad Request: invalid file_id")
        return self.files[file_id]

    async def send_typing(self, token: str, chat_id: str) -> None:
        pass
