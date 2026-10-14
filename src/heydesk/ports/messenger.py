"""The `MessengerApi` port: the few Graph API calls HeyDesk makes for Facebook Messenger (D60).

Every call takes the Page's access token: each business connects its own Page.
"""

from typing import Protocol


class MessengerUnavailable(Exception):
    """Facebook said no (a wrong token, the 24-hour window closed) or couldn't be reached.
    Never carries the token."""


class MessengerApi(Protocol):
    async def page_id(self, token: str) -> str:
        """The id of the Page the token is for. Fails for a wrong token."""
        ...

    async def subscribe(self, token: str, page_id: str) -> None:
        """Facebook posts the Page's new messages to our app's webhook."""
        ...

    async def unsubscribe(self, token: str, page_id: str) -> None: ...

    async def send_message(self, token: str, psid: str, text: str) -> None:
        """`psid` is the visitor's id on this Page."""
        ...

    async def send_typing(self, token: str, psid: str) -> None:
        """Shows "typing…" in the chat, until the reply or about 20 seconds."""
        ...
