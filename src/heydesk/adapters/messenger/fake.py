"""FakeMessenger: remembers what would have gone to Facebook, for tests. The token "bad" fails."""

from heydesk.ports.messenger import MessengerUnavailable


class FakeMessenger:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()  # page ids
        self.sent: list[tuple[str, str]] = []  # (psid, text), in order
        self.cards: list[tuple[str, list[dict[str, str]]]] = []  # (psid, cards), in order
        self.choices: list[tuple[str, list[str]]] = []  # (psid, quick replies), in order
        self.files: dict[str, bytes] = {}  # what a visitor's photo URL downloads as

    async def subscribe(self, token: str, page_id: str) -> None:
        if token.startswith("bad"):
            raise MessengerUnavailable("subscribed_apps: Invalid OAuth access token")
        self.subscribed.add(page_id)

    async def unsubscribe(self, token: str, page_id: str) -> None:
        self.subscribed.discard(page_id)

    async def send_message(
        self, token: str, psid: str, text: str, choices: list[str] | None = None
    ) -> None:
        self.sent.append((psid, text))
        if choices:
            self.choices.append((psid, choices))

    async def send_cards(self, token: str, psid: str, cards: list[dict[str, str]]) -> None:
        self.cards.append((psid, cards))

    async def download(self, url: str) -> bytes:
        if url not in self.files:
            raise MessengerUnavailable("attachment: Facebook didn't send the photo")
        return self.files[url]

    async def send_typing(self, token: str, psid: str) -> None:
        pass
