"""`uv run heydesk` starts the dev server. `create-user EMAIL PASSWORD` adds an owner to the
dev workspace, so the API can manage the dev agent. `create-admin EMAIL PASSWORD` makes a
platform admin (the only way to make one, D23). `reindex-knowledge` rebuilds the Qdrant chunks
from the documents saved in the database (D34)."""

import asyncio
import sys

import uvicorn

from heydesk.config import Settings
from heydesk.db import make_engine, utcnow
from heydesk.errors import HeyDeskError
from heydesk.models import Workspace
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.users import UserService
from heydesk.wiring import build_embedder, build_knowledge_store

USAGE = (
    "usage: heydesk [create-user EMAIL PASSWORD | create-admin EMAIL PASSWORD | reindex-knowledge]"
)


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


async def create_admin(email: str, password: str) -> None:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    await UserService(sessions, settings.jwt_secret, utcnow).create_admin(email, password)
    await engine.dispose()
    print(f"Created the admin {email}. Log in at POST /api/auth/login.")


async def reindex_knowledge() -> None:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    knowledge = KnowledgeService(
        sessions, build_embedder(settings), build_knowledge_store(settings), utcnow
    )
    count = await knowledge.reindex()
    await engine.dispose()
    print(f"Re-indexed {count} document(s).")


def main() -> None:
    match sys.argv[1:]:
        case []:
            uvicorn.run("heydesk.main:app", reload=True)
        case ["create-user", email, password]:
            try:
                asyncio.run(create_user(email, password))
            except HeyDeskError as error:
                sys.exit(error.message)
        case ["create-admin", email, password]:
            try:
                asyncio.run(create_admin(email, password))
            except HeyDeskError as error:
                sys.exit(error.message)
        case ["reindex-knowledge"]:
            try:
                asyncio.run(reindex_knowledge())
            except (HeyDeskError, RuntimeError) as error:
                sys.exit(str(error))
        case _:
            sys.exit(USAGE)
