"""`uv run heydesk` starts the dev server. `create-user EMAIL PASSWORD` adds an owner to the
dev workspace, so the API can manage the dev agent. `create-admin EMAIL [PASSWORD]` makes a
platform admin (the only way to make one, D23); `admins` lists them, `delete-admin EMAIL` removes
one, and `reset-password EMAIL [PASSWORD]` is for a forgotten password, an admin's or an owner's
(D67). Without a PASSWORD one is made up, and the password is printed: passwords are stored
hashed, so this is the only time it can be seen. `reindex-knowledge` rebuilds the Qdrant chunks
from the documents saved in the database (D34), and re-embeds every product photo (D57)."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn

from heydesk.config import Settings
from heydesk.db import make_engine, utcnow
from heydesk.errors import HeyDeskError
from heydesk.models import Workspace
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.services.users import UserService, temporary_password
from heydesk.wiring import (
    build_embedder,
    build_knowledge_store,
    build_mailer,
    build_media,
    build_photo_index,
    build_qdrant,
)

USAGE = """usage: heydesk [COMMAND]
  (none)                           the dev server
  create-user EMAIL PASSWORD       an owner in the dev business
  create-admin EMAIL [PASSWORD]    a platform admin; without a password, one is made up
  admins                           every admin
  delete-admin EMAIL               remove an admin
  reset-password EMAIL [PASSWORD]  a new password for an admin or an owner
  reindex-knowledge                rebuild the knowledge and photo search"""


@asynccontextmanager
async def user_service() -> AsyncIterator[UserService]:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    try:
        yield UserService(sessions, settings.jwt_secret, build_mailer(settings), utcnow)
    finally:
        await engine.dispose()


async def create_user(email: str, password: str) -> None:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    async with sessions() as s, s.begin():
        if await s.get(Workspace, DEV_WORKSPACE_ID) is None:
            s.add(Workspace(id=DEV_WORKSPACE_ID, name="Dev", created_at=utcnow()))
    token = await UserService(
        sessions, settings.jwt_secret, build_mailer(settings), utcnow
    ).create_user(DEV_WORKSPACE_ID, email, password)
    await engine.dispose()
    print(f"Created {email} in the dev workspace. A token for the API (7 days):\n{token}")


async def create_admin(email: str, password: str | None) -> None:
    password = password or temporary_password()
    async with user_service() as users:
        await users.create_admin(email, password)
    print(f"Created the admin {email}\npassword: {password}")


async def list_admins() -> None:
    async with user_service() as users:
        admins = await users.admins()
    for admin in admins:
        print(f"{admin.email}  (since {admin.created_at:%d %b %Y})")
    if not admins:
        print("No admins yet: heydesk create-admin EMAIL")


async def delete_admin(email: str) -> None:
    async with user_service() as users:
        await users.delete_admin(email)
    print(f"Deleted the admin {email}")


async def reset_password(email: str, password: str | None) -> None:
    password = password or temporary_password()
    async with user_service() as users:
        await users.reset_password(email, password)
    print(f"A new password for {email}\npassword: {password}")


async def reindex_knowledge() -> None:
    settings = Settings()
    engine, sessions = make_engine(settings.DATABASE_URL)
    embedder, qdrant = build_embedder(settings), build_qdrant(settings)
    knowledge = KnowledgeService(
        sessions, embedder, build_knowledge_store(settings, qdrant), utcnow
    )
    count = await knowledge.reindex()
    photos = PhotoService(
        embedder,
        build_photo_index(settings, qdrant),
        build_media(settings),
        settings.PHOTO_MIN_SIMILARITY,
    )
    photo_count = await photos.reindex()
    await engine.dispose()
    print(f"Re-indexed {count} document(s) and {photo_count} product photo(s).")


def main() -> None:
    match sys.argv[1:]:
        case []:
            uvicorn.run("heydesk.main:app", reload=True)
            return
        case ["create-user", email, password]:
            command = create_user(email, password)
        case ["create-admin", email]:
            command = create_admin(email, None)
        case ["create-admin", email, password]:
            command = create_admin(email, password)
        case ["admins"]:
            command = list_admins()
        case ["delete-admin", email]:
            command = delete_admin(email)
        case ["reset-password", email]:
            command = reset_password(email, None)
        case ["reset-password", email, password]:
            command = reset_password(email, password)
        case ["reindex-knowledge"]:
            command = reindex_knowledge()
        case _:
            sys.exit(USAGE)
    try:
        asyncio.run(command)
    except HeyDeskError as error:
        sys.exit(error.message)
    except RuntimeError as error:  # a setting is missing, such as GEMINI_API_KEY
        sys.exit(str(error))
