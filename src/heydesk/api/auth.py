"""Sign up, log in, who am I; a browser keeps the token in a cookie its scripts can't read,
and anything else sends it in `Authorization: Bearer …` (design §9).
The emailed codes verify an owner's email and reset a forgotten password."""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from heydesk.api.deps import SESSION_COOKIE, ClientIp, Svc, User
from heydesk.errors import NotFound
from heydesk.services.users import TOKEN_DAYS

router = APIRouter(tags=["auth"])


EMAIL = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
CODE = Field(pattern=r"^\s*\d{6}\s*$")
type Role = Literal["admin", "owner", "moderator"]
ROLES: dict[str, Role] = {"admin": "admin", "owner": "owner", "moderator": "moderator"}


class SignupIn(BaseModel):
    email: str = EMAIL
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)  # the business


class LoginIn(BaseModel):
    email: str
    password: str


class EmailIn(BaseModel):
    email: str = EMAIL


class CodeIn(BaseModel):
    email: str = EMAIL
    code: str = CODE


class ResetIn(BaseModel):
    email: str = EMAIL
    code: str = CODE
    new_password: str = Field(min_length=8, max_length=200)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class TokenOut(BaseModel):
    token: str


class BusinessOut(BaseModel):
    id: str
    name: str
    status: Literal["active", "suspended"]


class MeOut(BaseModel):
    id: str  # who "me" is when taking a chat
    email: str
    role: Role  # a moderator runs the inbox, not settings
    business: BusinessOut | None  # an owner's or moderator's business; None for an admin
    available: bool  # new hand-offs may go to them
    # opening it sends them alerts about their chats on Telegram; None without a bot
    telegram_link: str | None = None
    telegram_linked: bool = False


class AvailableIn(BaseModel):
    available: bool


def logged_in(request: Request, response: Response, token: str) -> TokenOut:
    """Sets the session cookie. HttpOnly: a script injected into the page can't read it.
    Strict: another site can't make the browser send it. Secure needs https, so it's off in
    development. The body still has the token, for Swagger and scripts."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=TOKEN_DAYS * 24 * 60 * 60,
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.APP_ENV == "production",
    )
    return TokenOut(token=token)


@router.post("/api/auth/signup", status_code=status.HTTP_204_NO_CONTENT)
async def signup(body: SignupIn, services: Svc, ip: ClientIp) -> None:
    """A new business and its owner, and an email with a code; `verify` logs them in.
    Admins are made from the command line, never here."""
    await services.rate_limits.signup(ip)  # 429: a script making businesses
    await services.users.signup(body.email, body.password, body.workspace_name)


@router.post("/api/auth/login")
async def login(
    body: LoginIn, request: Request, response: Response, services: Svc, ip: ClientIp
) -> TokenOut:
    """A 403 `email_not_verified` for the right password before the code is typed."""
    await services.rate_limits.login(ip, body.email)  # 429: password guessing
    return logged_in(request, response, await services.users.login(body.email, body.password))


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clears the session cookie, which the page's scripts can't do themselves."""
    response.delete_cookie(SESSION_COOKIE, path="/api")


@router.post("/api/auth/verify")
async def verify_email(
    body: CodeIn, request: Request, response: Response, services: Svc
) -> TokenOut:
    """The code emailed at signup → logged in. A 403 if it's wrong or expired."""
    await services.rate_limits.check_code(body.email)  # 429: guessing the code
    return logged_in(request, response, await services.users.verify_email(body.email, body.code))


@router.post("/api/auth/verify/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_code(body: EmailIn, services: Svc, ip: ClientIp) -> None:
    """Always a 204, so it doesn't say whether the email has an account."""
    await services.rate_limits.send_code(ip, body.email)
    await services.users.resend_code(body.email)


@router.post("/api/auth/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(body: EmailIn, services: Svc, ip: ClientIp) -> None:
    """Emails a reset code. Always a 204, so it doesn't say whether the email has an account."""
    await services.rate_limits.send_code(ip, body.email)
    await services.users.forgot_password(body.email)


@router.post("/api/auth/reset")
async def reset_password(
    body: ResetIn, request: Request, response: Response, services: Svc
) -> TokenOut:
    """The emailed code and a new password → logged in. A 403 if the code is wrong or expired.
    An invited moderator joins this way, with the code in their invite."""
    await services.rate_limits.check_code(body.email)  # 429: guessing the code
    token = await services.users.reset_with_code(body.email, body.code, body.new_password)
    return logged_in(request, response, token)


@router.put("/api/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordIn, current_user: User, services: Svc, ip: ClientIp
) -> None:
    """Your own password: the current one, then the new one. A 403 if the current is wrong."""
    await services.rate_limits.login(ip, current_user.user_id)  # 429: guessing the current one
    await services.users.change_password(
        current_user.user_id, body.current_password, body.new_password
    )


@router.get("/api/auth/me")
async def me(current_user: User, services: Svc) -> MeOut:
    user, business = await services.users.me(current_user)
    link, linked = (None, False)
    if user.workspace_id:
        link, linked = await services.team.member_alert_link(user.workspace_id, user.id)
    return MeOut(
        available=user.available,
        telegram_link=link,
        telegram_linked=linked,
        id=user.id,
        email=user.email,
        role=ROLES[user.role],
        business=BusinessOut(
            id=business.id,
            name=business.name,
            status="suspended" if business.status == "suspended" else "active",
        )
        if business
        else None,
    )


@router.put("/api/auth/me/available", status_code=status.HTTP_204_NO_CONTENT)
async def set_available(body: AvailableIn, current_user: User, services: Svc) -> None:
    """Switches yourself on or off for new hand-offs."""
    if current_user.workspace_id is None:
        raise NotFound("Only a business's team takes hand-offs.")
    await services.members.set_available(
        current_user.workspace_id, current_user.user_id, body.available
    )
