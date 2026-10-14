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

    async def page_id(self, token: str) -> str:
        me = await self._call("GET", "me", token)
        return str(me["id"])

    async def subscribe(self, token: str, page_id: str) -> None:
        await self._call(
            "POST", f"{page_id}/subscribed_apps", token, subscribed_fields="messages"
        )  # not echoes, reads or reactions

    async def unsubscribe(self, token: str, page_id: str) -> None:
        await self._call("DELETE", f"{page_id}/subscribed_apps", token)

    async def send_message(self, token: str, psid: str, text: str) -> None:
        await self._send(token, psid, message={"text": text}, messaging_type="RESPONSE")

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
