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
            "POST",
            f"{page_id}/subscribed_apps",
            token,
            subscribed_fields="messages,messaging_postbacks",
        )  # a card's button is a postback (D99); not echoes, reads or reactions

    async def unsubscribe(self, token: str, page_id: str) -> None:
        await self._call("DELETE", f"{page_id}/subscribed_apps", token)

    async def send_message(
        self, token: str, psid: str, text: str, choices: list[str] | None = None
    ) -> None:
        message: dict[str, Any] = {"text": text}
        if choices:
            message["quick_replies"] = [
                {"content_type": "text", "title": c, "payload": c} for c in choices
            ]
        await self._send(token, psid, message=message, messaging_type="RESPONSE")

    async def send_cards(self, token: str, psid: str, cards: list[dict[str, str]]) -> None:
        elements = []
        for c in cards:
            element: dict[str, Any] = {"title": c["name"][:80], "subtitle": c["price"][:80]}
            if c.get("url"):  # a doctor has no photo
                element["image_url"] = c["url"]
            if c.get("choice"):  # its tap is a postback with the choice as payload (D99)
                title = c.get("button") or "Order this"
                element["buttons"] = [{"type": "postback", "title": title, "payload": c["choice"]}]
            elements.append(element)
        payload = {
            "template_type": "generic",
            "image_aspect_ratio": "square",
            "elements": elements[:10],
        }
        message = {"attachment": {"type": "template", "payload": payload}}
        await self._send(token, psid, message=message, messaging_type="RESPONSE")

    async def send_link(self, token: str, psid: str, text: str, title: str, url: str) -> None:
        payload = {
            "template_type": "button",
            "text": text[:640],  # a button template's limit
            "buttons": [{"type": "web_url", "url": url, "title": title}],
        }
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

    async def pages(self, app_id: str, app_secret: str, user_token: str) -> list[dict[str, str]]:
        long = await self._call(
            "GET",
            "oauth/access_token",
            "",
            grant_type="fb_exchange_token",
            client_id=app_id,
            client_secret=app_secret,
            fb_exchange_token=user_token,
        )
        body = await self._call(
            "GET", "me/accounts", long["access_token"], fields="id,name,access_token", limit="100"
        )
        return [
            {"id": p["id"], "name": p.get("name") or p["id"], "token": p["access_token"]}
            for p in body.get("data", [])
        ]

    async def mark_seen(self, token: str, psid: str) -> None:
        await self._send(token, psid, sender_action="mark_seen")

    async def get_message(self, token: str, mid: str) -> dict[str, Any]:
        body = await self._call("GET", mid, token, fields="message,attachments")
        images = [a for a in (body.get("attachments") or {}).get("data", []) if "image_data" in a]
        return {
            "text": body.get("message") or "",
            "attachments": [
                {"type": "image", "payload": {"url": a["image_data"]["url"]}} for a in images
            ],
        }

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
