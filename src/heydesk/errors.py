import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class HeyDeskError(Exception):
    """An expected failure with an HTTP status and a stable code for clients."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class NotFound(HeyDeskError):
    status_code = 404
    code = "not_found"


class Unauthorized(HeyDeskError):
    status_code = 401
    code = "unauthorized"


class InvalidConfig(HeyDeskError):
    """An agent config that fails validation. `fields` maps a path to what's wrong with it."""

    status_code = 422
    code = "invalid_config"

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(f"{path}: {problem}" for path, problem in fields.items()))
        self.fields = fields


def _body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


async def _heydesk_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HeyDeskError)
    return JSONResponse(_body(exc.code, exc.message), status_code=exc.status_code)


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(_body("internal", "Something went wrong."), status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HeyDeskError, _heydesk_error)
    app.add_exception_handler(Exception, _unexpected)
