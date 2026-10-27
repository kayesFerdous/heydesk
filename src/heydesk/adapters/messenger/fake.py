"""FakeMessenger: remembers what would have gone to Facebook, for tests. The token "bad" fails."""

from typing import Any

from heydesk.ports.messenger import MessengerUnavailable


class FakeMessenger:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()  # page ids
        self.sent: list[tuple[str, str]] = []  # (psid, text), in order
        self.cards: list[tuple[str, list[dict[str, str]]]] = []  # (psid, cards), in order
        self.choices: list[tuple[str, list[str]]] = []  # (psid, quick replies), in order
        self.links: list[tuple[str, str, str, str]] = []  # (psid, text, button, url), in order
        self.files: dict[str, bytes] = {}  # what a visitor's photo URL downloads as
        self.actions: list[tuple[str, str]] = []  # (psid, "seen" or "typing"), in order
        self.messages: dict[str, dict[str, Any]] = {}  # what `get_message` finds, by mid
        # a Facebook login's Pages, by its user token (D118)
        self.logins: dict[str, list[dict[str, str]]] = {}
        self.posts: dict[str, dict[str, str]] = {}  # a post's text and picture, by id (D141)
        self.fetched: list[str] = []  # the posts asked for, in order
        self.public: list[tuple[str, str]] = []  # (comment id, text), in order
        self.private: list[tuple[str, str]] = []  # (comment id, text), in order
        self.private_cards: list[tuple[str, list[dict[str, str]]]] = []  # (comment id, cards)
        self.commenter = "7001"  # who a private reply reaches: their id on the Page

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

    async def send_cards(self, token: str, psid: str, cards: list[dict[str, str]]) -> str:
        self.cards.append((psid, cards))
        return f"m-cards-{len(self.cards)}"

    async def send_link(self, token: str, psid: str, text: str, title: str, url: str) -> None:
        self.links.append((psid, text, title, url))

    async def download(self, url: str) -> bytes:
        if url not in self.files:
            raise MessengerUnavailable("attachment: Facebook didn't send the photo")
        return self.files[url]

    async def send_typing(self, token: str, psid: str) -> None:
        self.actions.append((psid, "typing"))

    async def pages(self, app_id: str, app_secret: str, user_token: str) -> list[dict[str, str]]:
        if user_token not in self.logins:
            raise MessengerUnavailable("oauth/access_token: Invalid OAuth access token")
        return self.logins[user_token]

    async def mark_seen(self, token: str, psid: str) -> None:
        self.actions.append((psid, "seen"))

    async def get_post(self, token: str, post_id: str) -> dict[str, str]:
        self.fetched.append(post_id)
        return self.posts.get(post_id, {"text": "", "picture": ""})

    async def reply_public(self, token: str, comment_id: str, text: str) -> None:
        self.public.append((comment_id, text))

    async def reply_private(
        self, token: str, comment_id: str, text: str, cards: list[dict[str, str]] | None = None
    ) -> str:
        self.private.append((comment_id, text))
        if cards:
            self.private_cards.append((comment_id, cards))
        return self.commenter

    async def get_message(self, token: str, mid: str) -> dict[str, Any]:
        if mid not in self.messages:
            raise MessengerUnavailable(f"{mid}: no such message")
        return self.messages[mid]
