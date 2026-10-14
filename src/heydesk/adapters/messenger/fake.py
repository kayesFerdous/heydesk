"""FakeMessenger: remembers what would have gone to Facebook, for tests. The token "bad" fails."""

from heydesk.ports.messenger import MessengerUnavailable


class FakeMessenger:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()  # page ids
        self.sent: list[tuple[str, str]] = []  # (psid, text), in order

    async def page_id(self, token: str) -> str:
        if token.startswith("bad"):
            raise MessengerUnavailable("me: Invalid OAuth access token")
        return "1001"

    async def subscribe(self, token: str, page_id: str) -> None:
        self.subscribed.add(page_id)

    async def unsubscribe(self, token: str, page_id: str) -> None:
        self.subscribed.discard(page_id)

    async def send_message(self, token: str, psid: str, text: str) -> None:
        self.sent.append((psid, text))

    async def send_typing(self, token: str, psid: str) -> None:
        pass
