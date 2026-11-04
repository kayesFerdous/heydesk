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
    """A suspended business's chat: the link is there, the agent isn't answering."""

    code = "unavailable"


class NoVoice(Forbidden):
    """A visitor's call: the shop takes none for now."""

    code = "no_voice"


class EmailNotVerified(Forbidden):
    """The right password, but the emailed code isn't typed yet: the client asks for it."""

    code = "email_not_verified"


class Unauthorized(HeyDeskError):
    status_code = 401
    code = "unauthorized"


class Conflict(HeyDeskError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    """A status change a record doesn't allow, such as a collected order becoming ready."""

    code = "invalid_transition"


class UnreadableDocument(HeyDeskError):
    """A file we can't get text from, or a document that breaks a limit."""

    status_code = 422
    code = "unreadable_document"


class KnowledgeUnavailable(HeyDeskError):
    """The embedding service or Qdrant didn't answer. Nothing was saved: try again."""

    status_code = 503
    code = "knowledge_unavailable"


class UnreadablePhoto(HeyDeskError):
    """A file that isn't a picture we can read, one too big, or a photo sent to an agent that
    takes none."""

    status_code = 422
    code = "unreadable_photo"


class ConversationFull(HeyDeskError):
    """A conversation reached MAX_TURNS: every turn sends the whole transcript."""

    status_code = 409
    code = "conversation_full"


class VisitorLimited(HeyDeskError):
    """A visitor (or the whole business) used up today's turns, or was blocked for chatting
    off topic. The message says how to reach the business instead."""

    status_code = 429
    code = "visitor_limited"


class VisitorSilenced(VisitorLimited):
    """A blocked visitor who was told already: a messaging app sends nothing more. The web
    chat still gets the 429 with the message, as it shows the error itself."""


class TooFast(HeyDeskError):
    """Too many requests in a short time from one visitor, chat or address."""

    status_code = 429
    code = "rate_limited"


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


class AssistUnavailable(HeyDeskError):
    """The model didn't answer the team's request about a chat: try again."""

    status_code = 503
    code = "assist_unavailable"

    def __init__(self) -> None:
        super().__init__("The AI couldn't answer just now. Please try again.")
