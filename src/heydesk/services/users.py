"""Users: sign up, log in, and who a token belongs to (design §9).

A token carries the user (`sub`), their role and, for an owner, their business (`workspace_id`).
Every business route is scoped by it. An admin has no business of their own: they name
one per request, and the database (not the token) says they're still an admin.

Email codes: six digits that verify an owner's email after signup (no login until
then), or let them set a new password. An invited moderator joins with a reset code:
the invite emails one, and setting a password with it verifies the email. Nothing is stored: a code is an HMAC of the user, what it's for, their password
hash and a 15-minute window, and checking it makes it again. So it expires by itself, and a
reset code stops working once the password changes. The rate limits stop guessing.
"""

import hashlib
import hmac
import html
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, EmailNotVerified, Forbidden, NotFound, Unauthorized
from heydesk.models import AuditEvent, User, Workspace
from heydesk.ports.mailer import Mailer
from heydesk.services.billing import new_workspace

TOKEN_DAYS = 7
WRONG_CODE = "That code is wrong or has expired. Ask for a new one."
CODE_MINUTES = 15  # a code works in its window and the next: 15 to 30 minutes
type Purpose = Literal["verify", "reset"]
type Email = Literal["verify", "reset", "invite"]  # an invite carries a reset code
# Each email with a code: its subject, heading, the line above the code, the note below.
# BUSINESS and INVITER are filled in for an invite, last, as they're the owner's own words.
EMAILS: dict[Email, tuple[str, str, str, str]] = {
    "verify": (
        "Your HeyDesk code: CODE",
        "Verify your email",
        "Enter this code in HeyDesk to finish creating your account.",
        "If you didn't sign up for HeyDesk, you can ignore this email.",
    ),
    "reset": (
        "Your HeyDesk password code: CODE",
        "Set a new password",
        "Enter this code in HeyDesk to choose a new password.",
        "If you didn't ask for it, ignore this email: your password stays as it is.",
    ),
    "invite": (
        "Your code to join BUSINESS on HeyDesk: CODE",
        "Join BUSINESS on HeyDesk",
        (
            "You've been added to the team as a moderator. On HeyDesk's login page, choose "
            "Forgot password, then enter this email, this code and a new password."
        ),
        (
            "INVITER added you. After 15 minutes, Forgot password sends a new code. If you "
            "don't know them, ignore this email."
        ),
    ),
}
EMAIL_HTML = (Path(__file__).parent / "email_code.html").read_text(encoding="utf-8")
hasher = PasswordHash.recommended()  # argon2


@dataclass(frozen=True)
class CurrentUser:
    """Who a token says is calling. `workspace_id` is None only for an admin."""

    user_id: str
    role: str  # "admin" | "owner" | "moderator"
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

    async def signup(self, email: str, password: str, workspace_name: str) -> None:
        """A new business and its owner, in one transaction: a taken email leaves nothing behind.
        No token yet: it emails a code, and `verify_email` logs them in."""
        async with self.sessions() as s, s.begin():
            workspace = new_workspace(workspace_name, self.now())  # the free trial
            s.add(workspace)
            await s.flush()
            user = await self.new_user(
                s, email, password, role="owner", workspace_id=workspace.id, verified=False
            )
        await self.send_code(user, "verify")

    async def resend_code(self, email: str) -> None:
        """Another email with the verify code. An unknown or verified email gets nothing, and
        the route says nothing, so no one learns who has an account. An invited moderator
        gets nothing either: they join with a password (Forgot password)."""
        user = await self._by_email(email)
        if user is not None and user.role == "owner" and user.email_verified_at is None:
            await self.send_code(user, "verify")

    async def verify_email(self, email: str, code: str) -> str:
        """The code from the signup email → verified, and a token: their first login."""
        async with self.sessions() as s, s.begin():
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None:
                raise Forbidden(WRONG_CODE)
            if user.email_verified_at is None:
                self._check_code(user, "verify", code)
                user.email_verified_at = self.now()
        return self._token(user)

    async def forgot_password(self, email: str) -> None:
        """Emails a reset code. An unknown email does nothing and says nothing, so no one
        learns who has an account."""
        user = await self._by_email(email)
        if user is not None:
            await self.send_code(user, "reset")

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

    async def create_user(self, workspace_id: str, email: str, password: str) -> str:
        """An owner in an existing business, from the command line. Returns a token."""
        return self._token(
            await self._add(email, password, role="owner", workspace_id=workspace_id)
        )

    async def create_admin(self, email: str, password: str) -> None:
        """From the command line only (`heydesk create-admin`): never a web route."""
        await self._add(email, password, role="admin", workspace_id=None)

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
        user = await self._by_email(email)
        if user is None or not hasher.verify(password, user.password_hash):
            raise Unauthorized("Wrong email or password.")
        if user.email_verified_at is None:  # the right password, but no code typed yet
            raise EmailNotVerified("Verify your email first: type the code we sent you.")
        return self._token(user)

    async def _by_email(self, email: str) -> User | None:
        async with self.sessions() as s:
            return await s.scalar(select(User).where(User.email == email.strip().lower()))

    def user_from_token(self, token: str) -> CurrentUser:
        try:
            # Expiry is checked against our clock, not the machine's, so tests' fixed clock works
            claims = jwt.decode(
                token, self.secret, algorithms=["HS256"], options={"verify_exp": False}
            )
        except jwt.InvalidTokenError:
            claims = {}
        if not claims or claims.get("exp", 0) <= self.now().timestamp():
            raise Unauthorized("Log in again: the token is missing, wrong or expired.")
        return CurrentUser(str(claims["sub"]), str(claims["role"]), claims["ws"])

    async def still_admin(self, current_user: CurrentUser) -> bool:
        """The database's word, not the token's: a demoted admin is out at once."""
        if not current_user.is_admin:
            return False
        async with self.sessions() as s:
            role = await s.scalar(select(User.role).where(User.id == current_user.user_id))
        return role == "admin"

    async def still_moderator(self, current_user: CurrentUser) -> bool:
        """The database's word: a moderator the owner removed is out at once."""
        async with self.sessions() as s:
            role = await s.scalar(
                select(User.role).where(
                    User.id == current_user.user_id, User.workspace_id == current_user.workspace_id
                )
            )
        return role == "moderator"

    async def me(self, current_user: CurrentUser) -> tuple[User, Workspace | None]:
        async with self.sessions() as s:
            user = await s.get(User, current_user.user_id)
            if user is None:
                raise Unauthorized("Log in again.")
            business = await s.get(Workspace, user.workspace_id) if user.workspace_id else None
        return user, business

    async def set_password(self, user_id: str, password: str) -> None:
        """An admin resets an owner's forgotten password. Never another admin's: that would let
        one admin take over another, so an admin's is reset on the command line."""
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
        """Someone changes their own password, after a reset or any time."""
        async with self.sessions() as s, s.begin():
            user = await s.get(User, user_id)
            if user is None:
                raise Unauthorized("Log in again.")
            if not hasher.verify(current, user.password_hash):
                raise Forbidden("That isn't your current password.")
            user.password_hash = hasher.hash(new)

    async def _add(self, email: str, password: str, *, role: str, workspace_id: str | None) -> User:
        async with self.sessions() as s, s.begin():
            return await self.new_user(s, email, password, role=role, workspace_id=workspace_id)

    async def new_user(
        self,
        s: AsyncSession,
        email: str,
        password: str,
        *,
        role: str,
        workspace_id: str | None,
        verified: bool = True,  # signup's and invites need a code: the command line's are trusted
    ) -> User:
        """Adds a user in the caller's transaction; a 409 if the email is taken."""
        email = email.strip().lower()
        if await s.scalar(select(User.id).where(User.email == email)):
            raise Conflict("There's already an account with that email. Log in instead.")
        user = User(
            workspace_id=workspace_id,
            role=role,
            email=email,
            password_hash=hasher.hash(password),
            email_verified_at=self.now() if verified else None,
            created_at=self.now(),
        )
        s.add(user)
        return user

    async def send_code(
        self, user: User, email: Email, business: str = "", inviter: str = ""
    ) -> None:
        """An email with a code. The template's words are swapped from the code out, and the
        business's name last, so nothing an owner typed can be swapped again."""
        code = self._code(user, "verify" if email == "verify" else "reset", self._window())
        subject, heading, intro, note = EMAILS[email]

        def fill(line: str, escape: bool = False) -> str:
            name, who = (
                (html.escape(business), html.escape(inviter)) if escape else (business, inviter)
            )
            return line.replace("CODE", code).replace("INVITER", who).replace("BUSINESS", name)

        text = (
            f"{intro}\n\n{code}\n\nIt works for 15 minutes. Don't share it with anyone.\n\n{note}"
        )
        page = (
            EMAIL_HTML.replace("CODE", code)
            .replace("NOTE", fill(note, escape=True))
            .replace("INTRO", intro)
            .replace("HEADING", fill(heading, escape=True))
        )
        await self.mailer.send(user.email, fill(subject), fill(text), page)

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
