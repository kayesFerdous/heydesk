"""Sign up, log in, who am I; the token goes in `Authorization: Bearer …` (design §9)."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import ClientIp, Svc, User

router = APIRouter(tags=["auth"])


class SignupIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)  # the business


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str


class BusinessOut(BaseModel):
    id: str
    name: str
    status: Literal["active", "suspended"]


class MeOut(BaseModel):
    email: str
    role: Literal["admin", "owner"]
    business: BusinessOut | None  # an owner's business; None for an admin


@router.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, services: Svc) -> TokenOut:
    """A new business and its owner. Admins are made from the command line, never here."""
    return TokenOut(
        token=await services.users.signup(body.email, body.password, body.workspace_name)
    )


@router.post("/api/auth/login")
async def login(body: LoginIn, services: Svc, ip: ClientIp) -> TokenOut:
    await services.rate_limits.login(ip, body.email)  # 429: password guessing (D52)
    return TokenOut(token=await services.users.login(body.email, body.password))


@router.get("/api/auth/me")
async def me(principal: User, services: Svc) -> MeOut:
    user, business = await services.users.me(principal)
    return MeOut(
        email=user.email,
        role="admin" if user.role == "admin" else "owner",
        business=BusinessOut(
            id=business.id,
            name=business.name,
            status="suspended" if business.status == "suspended" else "active",
        )
        if business
        else None,
    )
