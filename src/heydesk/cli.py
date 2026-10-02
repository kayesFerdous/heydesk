"""`uv run heydesk` starts the dev server; `uv run heydesk create-user EMAIL PASSWORD`
adds a login to the dev workspace, so the API can manage the dev agent."""

import asyncio
import sys

import uvicorn

from heydesk.config import Settings
from heydesk.db import make_engine, utcnow
from heydesk.errors import HeyDeskError
from heydesk.models import Workspace
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.users import UserService

USAGE = "usage: heydesk [create-user EMAIL PASSWORD]"


async def create_user(email: str, password: str) -> None:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    async with sessions() as s, s.begin():
        if await s.get(Workspace, DEV_WORKSPACE_ID) is None:
            s.add(Workspace(id=DEV_WORKSPACE_ID, name="Dev", created_at=utcnow()))
    token = await UserService(sessions, settings.jwt_secret, utcnow).create_user(
        DEV_WORKSPACE_ID, email, password
    )
    await engine.dispose()
    print(f"Created {email} in the dev workspace. A token for the API (7 days):\n{token}")


def main() -> None:
    match sys.argv[1:]:
        case []:
            uvicorn.run("heydesk.main:app", reload=True)
        case ["create-user", email, password]:
            try:
                asyncio.run(create_user(email, password))
            except HeyDeskError as error:
                sys.exit(error.message)
        case _:
            sys.exit(USAGE)
