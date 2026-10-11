"""JSON log lines carrying the ids of whatever is running.

Ids only: never message text, names or phone numbers (D10). Pass extra fields
with `log.info("turn finished", extra={"conversation_id": "…"})`.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
workspace_id: ContextVar[str | None] = ContextVar("workspace_id", default=None)
conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)

_CONTEXT = (request_id, workspace_id, conversation_id)
_STANDARD = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for var in _CONTEXT:
            if (value := var.get()) is not None:
                line[var.name] = value
        line.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str, as_json: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if as_json else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):  # route uvicorn's own lines through ours
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # RequestLogMiddleware logs requests
    # its lines have Telegram tokens and SSLCommerz passwords in URLs
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RequestLogMiddleware:
    """Gives every HTTP request an id and logs one line when it finishes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = logging.getLogger("heydesk.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = uuid.uuid4().hex
        token = request_id.set(rid)
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            self.log.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id.reset(token)
