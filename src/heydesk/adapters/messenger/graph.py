"""MessengerGraph: Facebook's Graph API behind the `MessengerApi` port (D60), over httpx.

The token goes in the query string, so httpx's own request log is off (`logs.py`) and an error
never repeats the URL.
"""

from typing import Any

import httpx

from heydesk.ports.messenger import MessengerUnavailable

API = "https://graph.facebook.com/v23.0"


class MessengerGraph:
    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def subscribe(self, token: str, page_id: str) -> None:
        await self._call(
            "POST", f"{page_id}/subscribed_apps", token, subscribed_fields="messages"
        )  # not echoes, reads or reactions

    async def unsubscribe(self, token: str, page_id: str) -> None:
        await self._call("DELETE", f"{page_id}/subscribed_apps", token)

    async def send_message(self, token: str, psid: str, text: str) -> None:
        await self._send(token, psid, message={"text": text}, messaging_type="RESPONSE")

    async def send_cards(self, token: str, psid: str, cards: list[dict[str, str]]) -> None:
        elements = [
            {"title": c["name"], "subtitle": c["price"], "image_url": c["url"]} for c in cards
        ]
        payload = {"template_type": "generic", "image_aspect_ratio": "square", "elements": elements}
        message = {"attachment": {"type": "template", "payload": payload}}
        await self._send(token, psid, message=message, messaging_type="RESPONSE")

    async def download(self, url: str) -> bytes:
        try:
            res = await self.client.get(url, follow_redirects=True)
            res.raise_for_status()
        except httpx.HTTPError:  # the URL isn't in the error: it's the visitor's file
            raise MessengerUnavailable("attachment: Facebook didn't send the photo") from None
        return res.content

    async def send_typing(self, token: str, psid: str) -> None:
        await self._send(token, psid, sender_action="typing_on")

    async def _send(self, token: str, psid: str, **body: Any) -> None:
        await self._call("POST", "me/messages", token, json={"recipient": {"id": psid}, **body})

    async def _call(
        self, method: str, path: str, token: str, json: Any = None, **params: str
    ) -> Any:
        try:
            res = await self.client.request(
                method, f"{API}/{path}", params={"access_token": token, **params}, json=json
            )
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise MessengerUnavailable(f"{path}: Facebook didn't answer") from None
        if "error" in body:
            raise MessengerUnavailable(f"{path}: {body['error'].get('message', res.status_code)}")
        return body
