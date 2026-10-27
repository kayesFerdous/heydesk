"""FakeWhatsApp: remembers what would have gone to Meta, for tests. The token "bad" fails."""

from heydesk.ports.whatsapp import WhatsAppUnavailable


class FakeWhatsApp:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()  # phone ids
        self.sent: list[tuple[str, str]] = []  # (number, text), in order
        self.cards: list[tuple[str, list[dict[str, str]]]] = []  # (number, cards), in order
        self.choices: list[tuple[str, list[str]]] = []  # (number, buttons), in order
        self.links: list[tuple[str, str, str, str]] = []  # (number, text, button, url), in order
        self.lists: list[tuple[str, str, list[dict[str, str]]]] = []  # (number, text, rows)
        self.files: dict[str, bytes] = {}  # what a visitor's media id downloads as
        self.codes: dict[str, str] = {}  # Embedded Signup's code → the business's token
        self.registered: set[str] = set()  # phone ids

    async def exchange(self, app_id: str, app_secret: str, code: str) -> str:
        if code not in self.codes:
            raise WhatsAppUnavailable("oauth/access_token: Invalid verification code")
        return self.codes.pop(code)  # a code works once

    async def register(self, token: str, phone_id: str, pin: str) -> None:
        if token.startswith("bad"):
            raise WhatsAppUnavailable("register: Invalid OAuth access token")
        self.registered.add(phone_id)

    async def subscribe(self, token: str, account_id: str, phone_id: str) -> None:
        if token.startswith("bad"):
            raise WhatsAppUnavailable("subscribed_apps: Invalid OAuth access token")
        self.subscribed.add(phone_id)

    async def send_message(
        self, token: str, phone_id: str, to: str, text: str, choices: list[str] | None = None
    ) -> None:
        self.sent.append((to, text))
        if choices:
            self.choices.append((to, choices))

    async def send_cards(
        self, token: str, phone_id: str, to: str, cards: list[dict[str, str]]
    ) -> list[str]:
        self.cards.append((to, cards))
        return [f"wamid.card-{len(self.cards)}-{i}" for i in range(len(cards))]

    async def send_link(
        self, token: str, phone_id: str, to: str, text: str, title: str, url: str
    ) -> None:
        self.links.append((to, text, title, url))

    async def send_list(
        self, token: str, phone_id: str, to: str, text: str, rows: list[dict[str, str]]
    ) -> None:
        self.lists.append((to, text, rows))

    async def download(self, token: str, media_id: str) -> bytes:
        if media_id not in self.files:
            raise WhatsAppUnavailable("media: Meta didn't send the photo")
        return self.files[media_id]

    async def send_typing(self, token: str, phone_id: str, message_id: str) -> None:
        pass
