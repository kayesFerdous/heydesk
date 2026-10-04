import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)


class HeyDeskError(Exception):
    """An expected failure with an HTTP status and a stable code for clients."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class Forbidden(HeyDeskError):
    status_code = 403
    code = "forbidden"


class NotFound(HeyDeskError):
    status_code = 404
    code = "not_found"


class Unavailable(NotFound):
    """A suspended business's chat: the link is there, the agent isn't answering (D23)."""

    code = "unavailable"


class Unauthorized(HeyDeskError):
    status_code = 401
    code = "unauthorized"


class Conflict(HeyDeskError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    """A status change a record doesn't allow, such as a collected order becoming ready."""

    code = "invalid_transition"


class ConversationFull(HeyDeskError):
    """A conversation reached MAX_TURNS: every turn sends the whole transcript (D21)."""

    status_code = 409
    code = "conversation_full"


class InvalidConfig(HeyDeskError):
    """An agent config that fails validation. `fields` maps a path to what's wrong with it."""

    status_code = 422
    code = "invalid_config"

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(f"{path}: {problem}" for path, problem in fields.items()))
        self.fields = fields


def _body(code: str, message: str) -> dict[str, dict[str, Any]]:
    return {"error": {"code": code, "message": message}}


async def _heydesk_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HeyDeskError)
    body = _body(exc.code, exc.message)
    if isinstance(exc, InvalidConfig):
        body["error"]["fields"] = exc.fields
    return JSONResponse(body, status_code=exc.status_code)


async def _invalid_request(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own validation errors, in the same shape: a problem per field."""
    assert isinstance(exc, RequestValidationError)
    fields = {".".join(str(part) for part in e["loc"]): e["msg"] for e in exc.errors()}
    body = _body("invalid_request", "; ".join(f"{k}: {v}" for k, v in fields.items()))
    body["error"]["fields"] = fields
    return JSONResponse(body, status_code=422)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    """Starlette's errors (an unknown route, a wrong method) in the same shape."""
    assert isinstance(exc, HTTPException)
    code = "not_found" if exc.status_code == 404 else "http_error"
    return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(_body("internal", "Something went wrong."), status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HeyDeskError, _heydesk_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)
