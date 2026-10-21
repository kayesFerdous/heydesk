"""Email (D93): Brevo's API in production (free for 300 a day); without a key, the terminal.

Brevo sends from MAIL_FROM, an address verified in Brevo (Senders, domains). With your own
domain, add Brevo's SPF and DKIM records too, or Gmail puts the codes in spam.
"""

import logging

import httpx

log = logging.getLogger(__name__)


class Brevo:
    def __init__(self, api_key: str, sender: str, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(
            base_url="https://api.brevo.com/v3", headers={"api-key": api_key}, timeout=timeout
        )
        self.sender = sender

    async def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        body = {
            "sender": {"name": "HeyDesk", "email": self.sender},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
            **({"htmlContent": html} if html else {}),
        }
        try:
            response = await self.client.post("/smtp/email", json=body)
        except httpx.HTTPError as error:
            log.warning("email not sent", extra={"error": type(error).__name__})
            return
        if response.is_error:  # Brevo says why: a bad key, or an IP not in its authorised list
            log.warning("email not sent", extra={"status": response.status_code})


class ConsoleMailer:
    """Development and tests: prints each email and keeps it in `sent`, sending nothing."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (to, subject, text)

    async def send(self, to: str, subject: str, text: str, html: str = "") -> None:
        self.sent.append((to, subject, text))  # the html is the same words, in a layout
        print(f"--- email to {to}: {subject}\n{text}\n---", flush=True)
