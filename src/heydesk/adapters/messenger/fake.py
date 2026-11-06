"""FakeMessenger: remembers what would have gone to Facebook, for tests. The token "bad" fails."""

from typing import Any

from heydesk.ports.messenger import MessengerUnavailable


class FakeMessenger:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()  # page ids
        self.sent: list[tuple[str, str]] = []  # (chat_id, text), in order
        self.cards: list[tuple[str, list[dict[str, str]]]] = []  # (chat_id, cards), in order
        self.choices: list[tuple[str, list[str]]] = []  # (chat_id, quick replies), in order
        self.links: list[tuple[str, str, str, str]] = []  # (chat_id, text, button, url), in order
        self.files: dict[str, bytes] = {}  # what a visitor's photo URL downloads as
        self.actions: list[tuple[str, str]] = []  # (chat_id, "seen" or "typing"), in order
        self.messages: dict[str, dict[str, Any]] = {}  # what `get_message` finds, by mid
        # a Facebook login's Pages, by its user token
        self.logins: dict[str, list[dict[str, str]]] = {}

    async def subscribe(self, token: str, page_id: str) -> None:
        if token.startswith("bad"):
            raise MessengerUnavailable("subscribed_apps: Invalid OAuth access token")
        self.subscribed.add(page_id)

    async def unsubscribe(self, token: str, page_id: str) -> None:
        self.subscribed.discard(page_id)

    async def send_message(
        self, token: str, chat_id: str, text: str, choices: list[str] | None = None
    ) -> None:
        self.sent.append((chat_id, text))
        if choices:
            self.choices.append((chat_id, choices))

    async def send_cards(self, token: str, chat_id: str, cards: list[dict[str, str]]) -> str:
        self.cards.append((chat_id, cards))
        return f"m-cards-{len(self.cards)}"

    async def send_link(self, token: str, chat_id: str, text: str, title: str, url: str) -> None:
        self.links.append((chat_id, text, title, url))

    async def download(self, url: str) -> bytes:
        if url not in self.files:
            raise MessengerUnavailable("attachment: Facebook didn't send the photo")
        return self.files[url]

    async def send_typing(self, token: str, chat_id: str) -> None:
        self.actions.append((chat_id, "typing"))

    async def pages(self, app_id: str, app_secret: str, user_token: str) -> list[dict[str, str]]:
        if user_token not in self.logins:
            raise MessengerUnavailable("oauth/access_token: Invalid OAuth access token")
        return self.logins[user_token]

    async def mark_seen(self, token: str, chat_id: str) -> None:
        self.actions.append((chat_id, "seen"))

    async def get_message(self, token: str, message_id: str) -> dict[str, Any]:
        if message_id not in self.messages:
            raise MessengerUnavailable(f"{message_id}: no such message")
        return self.messages[message_id]
