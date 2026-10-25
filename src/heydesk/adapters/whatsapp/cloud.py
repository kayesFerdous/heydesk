"""WhatsAppCloud: Meta's WhatsApp Cloud API behind the `WhatsAppApi` port (D103), over httpx.

The token goes in the Authorization header, and an error never repeats it.
"""

from typing import Any

import httpx

from heydesk.adapters.messenger.graph import API
from heydesk.ports.whatsapp import WhatsAppUnavailable

BODY_LIMIT = 1024  # an interactive message's text; a plain one takes 4,096


class WhatsAppCloud:
    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def subscribe(self, token: str, account_id: str, phone_id: str) -> None:
        await self._call("GET", phone_id, token)  # the number is this token's
        await self._call("POST", f"{account_id}/subscribed_apps", token)

    async def send_message(
        self, token: str, phone_id: str, to: str, text: str, choices: list[str] | None = None
    ) -> None:
        if not choices or len(text) > BODY_LIMIT:  # too long for buttons: they're typed
            await self._send(token, phone_id, to, type="text", text={"body": text})
            return
        if len(choices) > 3:  # a list; its rows' titles are the choices
            await self.send_list(
                token, phone_id, to, text, [{"id": c, "title": c} for c in choices]
            )
            return
        buttons = [{"type": "reply", "reply": {"id": c, "title": c}} for c in choices]
        interactive = {"type": "button", "body": {"text": text}, "action": {"buttons": buttons}}
        await self._send(token, phone_id, to, type="interactive", interactive=interactive)

    async def send_link(
        self, token: str, phone_id: str, to: str, text: str, title: str, url: str
    ) -> None:
        interactive = {
            "type": "cta_url",
            "body": {"text": text[:BODY_LIMIT]},
            "action": {"name": "cta_url", "parameters": {"display_text": title, "url": url}},
        }
        await self._send(token, phone_id, to, type="interactive", interactive=interactive)

    async def send_list(
        self, token: str, phone_id: str, to: str, text: str, rows: list[dict[str, str]]
    ) -> None:
        action = {"button": "Choose", "sections": [{"rows": rows[:10]}]}  # a list holds 10
        interactive = {"type": "list", "body": {"text": text}, "action": action}
        await self._send(token, phone_id, to, type="interactive", interactive=interactive)

    async def send_cards(
        self, token: str, phone_id: str, to: str, cards: list[dict[str, str]]
    ) -> None:
        for c in cards:
            caption = f"{c['name']} · {c['price']}"
            if c.get("page"):  # a product's page (D121): WhatsApp opens a link in a message
                caption += f"\nDetails: {c['page']}"
            if c.get("video"):  # its first video (D125)
                caption += f"\nVideo: {c['video']}"
            if not c.get("choice"):
                image = {"link": c["url"], "caption": caption}
                await self._send(token, phone_id, to, type="image", image=image)
                continue
            # its tap is a button reply with the choice as its id (D99)
            button = {"type": "reply", "reply": {"id": c["choice"], "title": "Order this"}}
            interactive = {
                "type": "button",
                "header": {"type": "image", "image": {"link": c["url"]}},
                "body": {"text": caption},
                "action": {"buttons": [button]},
            }
            await self._send(token, phone_id, to, type="interactive", interactive=interactive)

    async def download(self, token: str, media_id: str) -> bytes:
        media = await self._call("GET", media_id, token)
        try:  # Meta's file URL needs the token too
            res = await self.client.get(media["url"], headers=self._auth(token))
            res.raise_for_status()
        except (httpx.HTTPError, KeyError):  # the URL isn't in the error: it's the visitor's file
            raise WhatsAppUnavailable("media: Meta didn't send the photo") from None
        return res.content

    async def send_typing(self, token: str, phone_id: str, message_id: str) -> None:
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        await self._call("POST", f"{phone_id}/messages", token, json=body)

    async def _send(self, token: str, phone_id: str, to: str, **message: Any) -> None:
        body = {"messaging_product": "whatsapp", "to": to, **message}
        await self._call("POST", f"{phone_id}/messages", token, json=body)

    async def _call(self, method: str, path: str, token: str, json: Any = None) -> Any:
        try:
            res = await self.client.request(
                method, f"{API}/{path}", headers=self._auth(token), json=json
            )
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise WhatsAppUnavailable(f"{path}: Meta didn't answer") from None
        if "error" in body:
            raise WhatsAppUnavailable(f"{path}: {body['error'].get('message', res.status_code)}")
        return body

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}
