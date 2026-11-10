"""Log lines carrying the ids of whatever is running.

Production writes one JSON line per log (`LOG_JSON=true`); development writes lines a
person can read, with the same details. Ids only: never message text, names or phone
numbers. Pass extra fields with `log.info("call done", extra={"call_id": "…"})`.
"""

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_STANDARD = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """The fields the code passed with `extra=`."""
    return {k: v for k, v in vars(record).items() if k not in _STANDARD}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (rid := request_id.get()) is not None:
            line["request_id"] = rid
        line.update(_extras(record))
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


# Terminal colours for the readable lines
_DIM = "\033[2m"
_RESET = "\033[0m"
_LEVEL_COLOURS = {
    "DEBUG": _DIM,
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[1;31m",  # bold red
}


class ReadableFormatter(logging.Formatter):
    """One line a person can read, in the server's local time:

        14:02:30  INFO     request        GET /api/agent  status=200 ms=8.4

    Nested details (a turn's whole trace) are left to the JSON lines: the message sums them up.
    """

    def __init__(self, colour: bool) -> None:
        super().__init__()
        self.colour = colour

    def paint(self, text: str, colour: str) -> str:
        return f"{colour}{text}{_RESET}" if self.colour else text

    def format(self, record: logging.LogRecord) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = self.paint(f"{record.levelname:<8}", _LEVEL_COLOURS.get(record.levelname, ""))
        # heydesk.services.calls → calls. Uvicorn writes even its normal lines as uvicorn.error
        source = "uvicorn" if record.name.startswith("uvicorn") else record.name.rsplit(".", 1)[-1]
        line = f"{self.paint(when, _DIM)}  {level} {source:<13}  {record.getMessage()}"
        details = [
            f"{self.paint(key + '=', _DIM)}{value}"
            for key, value in _extras(record).items()
            if not isinstance(value, dict | list)
        ]
        if details:
            line += "  " + " ".join(details)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str, as_json: bool) -> None:
    handler = logging.StreamHandler()
    if as_json:
        handler.setFormatter(JsonFormatter())
    else:  # colours on a terminal, or through scripts/dev.sh's pipe, which sets FORCE_COLOR
        colour = handler.stream.isatty() or bool(os.environ.get("FORCE_COLOR"))
        handler.setFormatter(ReadableFormatter(colour))
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
    """Gives every HTTP request an id and logs one line when it finishes:
    `GET /api/agent` with its status and time. A 4xx is a warning and a 5xx an error,
    so a failed request stands out."""

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
            ms = round((time.perf_counter() - started) * 1000, 1)
            if status >= 500:
                level = logging.ERROR
            elif status >= 400:
                level = logging.WARNING
            else:
                level = logging.INFO
            if not (scope["path"] == "/health" and status == 200):  # Render checks it all day
                method, path = scope["method"], scope["path"]
                self.log.log(level, "%s %s", method, path, extra={"status": status, "ms": ms})
            request_id.reset(token)
