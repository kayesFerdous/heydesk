"""Sign up and log in; the token goes in `Authorization: Bearer …` (design §9)."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from heydesk.api.deps import get_services
from heydesk.services import Services

router = APIRouter(tags=["auth"])

type Svc = Annotated[Services, Depends(get_services)]


class SignupIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)  # the business


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str


@router.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, services: Svc) -> TokenOut:
    return TokenOut(
        token=await services.users.signup(body.email, body.password, body.workspace_name)
    )


@router.post("/api/auth/login")
async def login(body: LoginIn, services: Svc) -> TokenOut:
    return TokenOut(token=await services.users.login(body.email, body.password))
