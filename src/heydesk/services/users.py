"""Users: sign up, log in, and who a token belongs to (design §9, D23).

A token carries the user (`sub`), their role and, for an owner, their business (`ws`).
Every business route is scoped by it. An admin has no business of their own: they name
one per request, and the database (not the token) says they're still an admin.

Email codes (D93): six digits that verify an owner's email after signup, or let them set a
new password. Nothing is stored: a code is an HMAC of the user, what it's for, their password
hash and a 15-minute window, and checking it makes it again. So it expires by itself, and a
reset code stops working once the password changes. The rate limits stop guessing.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, Forbidden, NotFound, Unauthorized
from heydesk.models import AuditEvent, User, Workspace
from heydesk.ports.mailer import Mailer

TOKEN_DAYS = 7
WRONG_CODE = "That code is wrong or has expired. Ask for a new one."
CODE_MINUTES = 15  # a code works in its window and the next: 15 to 30 minutes
type Purpose = Literal["verify", "reset"]
EMAILS: dict[Purpose, tuple[str, str]] = {  # subject, text; CODE is replaced
    "verify": (
        "Your HeyDesk code: CODE",
        (
            "Your code to verify your email is CODE. It works for 15 minutes.\n\n"
            "If you didn't sign up for HeyDesk, you can ignore this email."
        ),
    ),
    "reset": (
        "Your HeyDesk password code: CODE",
        (
            "Your code to set a new password is CODE. It works for 15 minutes.\n\n"
            "If you didn't ask for it, ignore this email: your password stays as it is."
        ),
    ),
}
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
        mailer: Mailer,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.secret = secret
        self.mailer = mailer
        self.now = now

    async def signup(self, email: str, password: str, workspace_name: str) -> str:
        """A new business and its owner, in one transaction: a taken email leaves nothing behind.
        Returns a token, and emails a code: the agent answers once they type it (D93)."""
        async with self.sessions() as s, s.begin():
            workspace = Workspace(name=workspace_name, created_at=self.now())
            s.add(workspace)
            await s.flush()
            user = await self._new_user(
                s, email, password, role="owner", ws=workspace.id, verified=False
            )
        await self._send_code(user, "verify")
        return self._token(user)

    async def resend_code(self, user_id: str) -> None:
        """A new email with the verify code; nothing if they're already verified."""
        async with self.sessions() as s:
            user = await s.get(User, user_id)
        if user is None:
            raise Unauthorized("Log in again.")
        if user.email_verified_at is None:
            await self._send_code(user, "verify")

    async def verify_email(self, user_id: str, code: str) -> None:
        async with self.sessions() as s, s.begin():
            user = await s.get(User, user_id)
            if user is None:
                raise Unauthorized("Log in again.")
            if user.email_verified_at is None:
                self._check_code(user, "verify", code)
                user.email_verified_at = self.now()

    async def forgot_password(self, email: str) -> None:
        """Emails a reset code. An unknown email does nothing and says nothing, so no one
        learns who has an account."""
        async with self.sessions() as s:
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is not None:
            await self._send_code(user, "reset")

    async def reset_with_code(self, email: str, code: str, password: str) -> str:
        """A new password from the emailed code; the email is verified too (they read it).
        Returns a token, so they're logged in."""
        async with self.sessions() as s, s.begin():
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None:
                raise Forbidden(WRONG_CODE)
            self._check_code(user, "reset", code)
            user.password_hash = hasher.hash(password)
            user.email_verified_at = user.email_verified_at or self.now()
        return self._token(user)

    async def create_user(self, ws: str, email: str, password: str) -> str:
        """An owner in an existing business, from the command line. Returns a token."""
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
        self,
        s: AsyncSession,
        email: str,
        password: str,
        *,
        role: str,
        ws: str | None,
        verified: bool = True,  # only signup's need a code: the command line's are trusted
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
            email_verified_at=self.now() if verified else None,
            created_at=self.now(),
        )
        s.add(user)
        return user

    async def _send_code(self, user: User, purpose: Purpose) -> None:
        code = self._code(user, purpose, self._window())
        subject, text = EMAILS[purpose]
        await self.mailer.send(
            user.email, subject.replace("CODE", code), text.replace("CODE", code)
        )

    def _check_code(self, user: User, purpose: Purpose, code: str) -> None:
        window = self._window()
        if not any(
            hmac.compare_digest(code.strip(), self._code(user, purpose, w))
            for w in (window, window - 1)
        ):
            raise Forbidden(WRONG_CODE)

    def _code(self, user: User, purpose: Purpose, window: int) -> str:
        message = f"{purpose}:{user.id}:{user.password_hash}:{window}".encode()
        digest = hmac.new(self.secret.encode(), message, hashlib.sha256).digest()
        return f"{int.from_bytes(digest[:8]) % 1_000_000:06d}"

    def _window(self) -> int:
        return int(self.now().timestamp()) // (CODE_MINUTES * 60)

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
