"""Tells an open inbox that a chat changed, so it needn't be refreshed.

A page that has the inbox open holds one stream. When a committed change touches a
conversation, the workspace's open pages get `{"conversation_id": ...}` and fetch that chat. With
no page open, nothing happens: no query, no timer. The pages are kept in this process's
memory, like the `/phone` pages; more than one process needs Postgres LISTEN/NOTIFY
or Redis behind `publish`.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from heydesk.models import Conversation

QUEUE_SIZE = 100  # a page this far behind loses events; it fetches the whole list again


class LiveHub:
    def __init__(self) -> None:
        self.pages: dict[str, set[asyncio.Queue[str]]] = {}  # open inboxes, by workspace

    def publish(self, ws: str, conversation_id: str) -> None:
        for queue in self.pages.get(ws, ()):
            if queue.full():
                queue.get_nowait()  # the oldest goes; the page still hears about the newest
            queue.put_nowait(conversation_id)

    @contextmanager
    def listen(self, ws: str) -> Iterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_SIZE)
        self.pages.setdefault(ws, set()).add(queue)
        try:
            yield queue
        finally:
            pages = self.pages[ws]
            pages.discard(queue)
            if not pages:
                del self.pages[ws]


def watch(sessions: async_sessionmaker[AsyncSession], hub: LiveHub) -> None:
    """Publish every conversation a session changed, after its commit. One place sees every
    write: the visitor's turn, the team's reply, assigning, Done, labels, a take-over."""
    session_class: type[Session] = sessions.kw["sync_session_class"]

    @event.listens_for(session_class, "after_flush")
    def collect(session: Session, _: Any) -> None:
        changed: set[tuple[str, str]] = session.info.setdefault("changed", set())
        for row in (*session.new, *session.dirty):
            if isinstance(row, Conversation):
                changed.add((row.workspace_id, row.id))

    @event.listens_for(session_class, "after_commit")
    def publish(session: Session) -> None:
        for ws, conversation_id in session.info.pop("changed", ()):
            hub.publish(ws, conversation_id)

    @event.listens_for(session_class, "after_rollback")
    def forget(session: Session) -> None:
        session.info.pop("changed", None)
