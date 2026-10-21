"""Sign up, log in, who am I; the token goes in `Authorization: Bearer …` (design §9).
The emailed codes verify an owner's email and reset a forgotten password (D93)."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import ClientIp, Svc, User

router = APIRouter(tags=["auth"])


EMAIL = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
CODE = Field(pattern=r"^\s*\d{6}\s*$")


class SignupIn(BaseModel):
    email: str = EMAIL
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)  # the business


class LoginIn(BaseModel):
    email: str
    password: str


class CodeIn(BaseModel):
    code: str = CODE


class ForgotIn(BaseModel):
    email: str = EMAIL


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
    email: str
    email_verified: bool  # False: their agent doesn't answer until they type the emailed code
    role: Literal["admin", "owner"]
    business: BusinessOut | None  # an owner's business; None for an admin


@router.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, services: Svc, ip: ClientIp) -> TokenOut:
    """A new business and its owner. Admins are made from the command line, never here."""
    await services.rate_limits.signup(ip)  # 429: a script making businesses
    return TokenOut(
        token=await services.users.signup(body.email, body.password, body.workspace_name)
    )


@router.post("/api/auth/login")
async def login(body: LoginIn, services: Svc, ip: ClientIp) -> TokenOut:
    await services.rate_limits.login(ip, body.email)  # 429: password guessing (D52)
    return TokenOut(token=await services.users.login(body.email, body.password))


@router.post("/api/auth/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: CodeIn, principal: User, services: Svc) -> None:
    """The code emailed at signup; a 403 if it's wrong or expired."""
    await services.rate_limits.check_code(principal.user_id)  # 429: guessing the code
    await services.users.verify_email(principal.user_id, body.code)


@router.post("/api/auth/verify/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_code(principal: User, services: Svc, ip: ClientIp) -> None:
    await services.rate_limits.send_code(ip, principal.user_id)
    await services.users.resend_code(principal.user_id)


@router.post("/api/auth/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(body: ForgotIn, services: Svc, ip: ClientIp) -> None:
    """Emails a reset code. Always a 204, so it doesn't say whether the email has an account."""
    await services.rate_limits.send_code(ip, body.email)
    await services.users.forgot_password(body.email)


@router.post("/api/auth/reset")
async def reset_password(body: ResetIn, services: Svc) -> TokenOut:
    """The emailed code and a new password → logged in. A 403 if the code is wrong or expired."""
    await services.rate_limits.check_code(body.email)  # 429: guessing the code
    return TokenOut(
        token=await services.users.reset_with_code(body.email, body.code, body.new_password)
    )


@router.put("/api/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordIn, principal: User, services: Svc, ip: ClientIp) -> None:
    """Your own password: the current one, then the new one. A 403 if the current is wrong."""
    await services.rate_limits.login(ip, principal.user_id)  # 429: guessing the current one
    await services.users.change_password(
        principal.user_id, body.current_password, body.new_password
    )


@router.get("/api/auth/me")
async def me(principal: User, services: Svc) -> MeOut:
    user, business = await services.users.me(principal)
    return MeOut(
        email=user.email,
        email_verified=user.email_verified_at is not None,
        role="admin" if user.role == "admin" else "owner",
        business=BusinessOut(
            id=business.id,
            name=business.name,
            status="suspended" if business.status == "suspended" else "active",
        )
        if business
        else None,
    )
