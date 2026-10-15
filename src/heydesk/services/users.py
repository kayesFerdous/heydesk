"""Users: sign up, log in, and who a token belongs to (design §9, D23).

A token carries the user (`sub`), their role and, for an owner, their business (`ws`).
Every business route is scoped by it. An admin has no business of their own: they name
one per request, and the database (not the token) says they're still an admin.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, Forbidden, NotFound, Unauthorized
from heydesk.models import AuditEvent, User, Workspace

TOKEN_DAYS = 7
hasher = PasswordHash.recommended()  # argon2


@dataclass(frozen=True)
class Principal:
    """Who a token says is calling. `workspace_id` is None only for an admin."""

    user_id: str
    role: str  # "admin" | "owner"
    workspace_id: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class UserService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        secret: str,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.secret = secret
        self.now = now

    async def signup(self, email: str, password: str, workspace_name: str) -> str:
        """A new business and its owner, in one transaction: a taken email leaves nothing behind.
        Returns a token."""
        async with self.sessions() as s, s.begin():
            workspace = Workspace(name=workspace_name, created_at=self.now())
            s.add(workspace)
            await s.flush()
            user = await self._new_user(s, email, password, role="owner", ws=workspace.id)
        return self._token(user)

    async def create_user(self, ws: str, email: str, password: str) -> str:
        """An owner in an existing business. Returns a token."""
        return self._token(await self._add(email, password, role="owner", ws=ws))

    async def create_admin(self, email: str, password: str) -> None:
        """From the command line only (`heydesk create-admin`): never a web route."""
        await self._add(email, password, role="admin", ws=None)

    async def admins(self) -> list[User]:
        """From the command line only (`heydesk admins`)."""
        async with self.sessions() as s:
            query = select(User).where(User.role == "admin").order_by(User.created_at)
            return list(await s.scalars(query))

    async def delete_admin(self, email: str) -> None:
        """From the command line only. An admin in the audit log stays: it keeps who did what."""
        async with self.sessions() as s, s.begin():
            user = await s.scalar(
                select(User).where(User.email == email.strip().lower(), User.role == "admin")
            )
            if user is None:
                raise NotFound("There's no admin with that email.")
            if await s.scalar(select(AuditEvent.id).where(AuditEvent.actor_id == user.id).limit(1)):
                raise Conflict(
                    "This admin is in the audit log, which keeps who did what, so they stay. "
                    "Reset their password instead."
                )
            await s.delete(user)

    async def reset_password(self, email: str, password: str) -> None:
        """From the command line, for a forgotten password: an admin's or an owner's."""
        async with self.sessions() as s, s.begin():
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None:
                raise NotFound("There's no user with that email.")
            user.password_hash = hasher.hash(password)

    async def login(self, email: str, password: str) -> str:
        async with self.sessions() as s:
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not hasher.verify(password, user.password_hash):
            raise Unauthorized("Wrong email or password.")
        return self._token(user)

    def principal(self, token: str) -> Principal:
        try:
            # Expiry is checked against our clock, not the machine's, so tests' fixed clock works
            claims = jwt.decode(
                token, self.secret, algorithms=["HS256"], options={"verify_exp": False}
            )
        except jwt.InvalidTokenError:
            claims = {}
        if not claims or claims.get("exp", 0) <= self.now().timestamp():
            raise Unauthorized("Log in again: the token is missing, wrong or expired.")
        return Principal(str(claims["sub"]), str(claims["role"]), claims["ws"])

    async def still_admin(self, principal: Principal) -> bool:
        """The database's word, not the token's: a demoted admin is out at once."""
        if not principal.is_admin:
            return False
        async with self.sessions() as s:
            role = await s.scalar(select(User.role).where(User.id == principal.user_id))
        return role == "admin"

    async def me(self, principal: Principal) -> tuple[User, Workspace | None]:
        async with self.sessions() as s:
            user = await s.get(User, principal.user_id)
            if user is None:
                raise Unauthorized("Log in again.")
            business = await s.get(Workspace, user.workspace_id) if user.workspace_id else None
        return user, business

    async def set_password(self, user_id: str, password: str) -> None:
        """An admin resets an owner's forgotten password. Never another admin's: that would let
        one admin take over another, so an admin's is reset on the command line (D68)."""
        async with self.sessions() as s, s.begin():
            user = await s.get(User, user_id)
            if user is None:
                raise NotFound("There's no such user.")
            if user.role == "admin":
                raise Forbidden(
                    "An admin's password is reset on the command line: heydesk reset-password EMAIL"
                )
            user.password_hash = hasher.hash(password)

    async def change_password(self, user_id: str, current: str, new: str) -> None:
        """Someone changes their own password, after a reset or any time (D68)."""
        async with self.sessions() as s, s.begin():
            user = await s.get(User, user_id)
            if user is None:
                raise Unauthorized("Log in again.")
            if not hasher.verify(current, user.password_hash):
                raise Forbidden("That isn't your current password.")
            user.password_hash = hasher.hash(new)

    async def _add(self, email: str, password: str, *, role: str, ws: str | None) -> User:
        async with self.sessions() as s, s.begin():
            return await self._new_user(s, email, password, role=role, ws=ws)

    async def _new_user(
        self, s: AsyncSession, email: str, password: str, *, role: str, ws: str | None
    ) -> User:
        """Adds a user in the caller's transaction; a 409 if the email is taken."""
        email = email.strip().lower()
        if await s.scalar(select(User.id).where(User.email == email)):
            raise Conflict("There's already an account with that email. Log in instead.")
        user = User(
            workspace_id=ws,
            role=role,
            email=email,
            password_hash=hasher.hash(password),
            created_at=self.now(),
        )
        s.add(user)
        return user

    def _token(self, user: User) -> str:
        claims = {
            "sub": user.id,
            "role": user.role,
            "ws": user.workspace_id,
            "exp": self.now() + timedelta(days=TOKEN_DAYS),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")


def temporary_password() -> str:
    """Shown once to the admin who reset it; the owner changes it after logging in
    (PUT /api/auth/password)."""
    return secrets.token_urlsafe(9)
