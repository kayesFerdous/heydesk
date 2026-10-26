"""The `MessengerApi` port: the few Graph API calls HeyDesk makes for Facebook Messenger (D60).

Every call takes the Page's access token: each business connects its own Page.
"""

from typing import Any, Protocol

# Every message we send carries it as `metadata`, and so does its echo: an echo without it is
# the Page's own, written in Business Suite or Messenger by the owner (D135)
OURS = "heydesk"


class MessengerUnavailable(Exception):
    """Facebook said no (a wrong token, the 24-hour window closed) or couldn't be reached.
    Never carries the token."""


class MessengerApi(Protocol):
    async def subscribe(self, token: str, page_id: str) -> None:
        """Facebook posts the Page's new messages to our app's webhook. Fails when the token
        isn't for that Page, so it is also the check on the pair."""
        ...

    async def unsubscribe(self, token: str, page_id: str) -> None: ...

    async def send_message(
        self, token: str, psid: str, text: str, choices: list[str] | None = None
    ) -> None:
        """`psid` is the visitor's id on this Page. `choices` are quick replies (D99): a tap
        comes back as a message with the choice as its text."""
        ...

    async def send_cards(self, token: str, psid: str, cards: list[dict[str, str]]) -> None:
        """Products as one carousel (D62): each card `{name, price, url}`, the photo's full URL,
        and maybe `choice`: an Order button whose tap comes back as a postback with it (D99).
        Doctors use it too (D111): no `url`, `price` is the line under the name, and `button`
        names the button ("Book"; a product's is "Order this"). A product's `page` and `video`
        are link buttons (D121, D125); a card with no `url` has no photo."""
        ...

    async def send_link(self, token: str, psid: str, text: str, title: str, url: str) -> None:
        """Text with one button that opens `url` (D111): a receipt's Pay button."""
        ...

    async def download(self, url: str) -> bytes:
        """A photo the visitor sent, from the URL in its message. Facebook's URLs expire."""
        ...

    async def send_typing(self, token: str, psid: str) -> None:
        """Shows "typing…" in the chat, until the reply or about 20 seconds."""
        ...

    async def pages(self, app_id: str, app_secret: str, user_token: str) -> list[dict[str, str]]:
        """The Pages a Facebook login manages (D118), each `{id, name, token}`. The login's
        short token is first made a long-lived one, so the Pages' tokens don't expire."""
        ...

    async def mark_seen(self, token: str, psid: str) -> None:
        """Shows the visitor their messages as "Seen" (D117)."""
        ...

    async def get_message(self, token: str, mid: str) -> dict[str, Any]:
        """A message of the Page's chats by its id, the visitor's or the Page's own, in the
        webhook's shape: `text`, and `attachments` with an image's `payload.url` (D117)."""
        ...
