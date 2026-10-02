"""Users: sign up, log in, and the workspace a token belongs to (design §9)."""

from collections.abc import Callable
from datetime import datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, Unauthorized
from heydesk.models import User, Workspace

TOKEN_DAYS = 7
hasher = PasswordHash.recommended()  # argon2


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
        """A new workspace and its first user. Returns a token."""
        workspace = Workspace(name=workspace_name, created_at=self.now())
        async with self.sessions() as s, s.begin():
            s.add(workspace)
            await s.flush()
        return await self.create_user(workspace.id, email, password)

    async def create_user(self, ws: str, email: str, password: str) -> str:
        """A user in an existing workspace. Returns a token."""
        email = email.strip().lower()
        async with self.sessions() as s, s.begin():
            if await s.scalar(select(User.id).where(User.email == email)):
                raise Conflict("There's already an account with that email. Log in instead.")
            user = User(
                workspace_id=ws,
                email=email,
                password_hash=hasher.hash(password),
                created_at=self.now(),
            )
            s.add(user)
        return self._token(user)

    async def login(self, email: str, password: str) -> str:
        async with self.sessions() as s:
            user = await s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not hasher.verify(password, user.password_hash):
            raise Unauthorized("Wrong email or password.")
        return self._token(user)

    def workspace_of(self, token: str) -> str:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            raise Unauthorized("Log in again: the token is missing, wrong or expired.") from None
        return str(claims["ws"])

    def _token(self, user: User) -> str:
        claims = {
            "sub": user.id,
            "ws": user.workspace_id,
            "exp": self.now() + timedelta(days=TOKEN_DAYS),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")
