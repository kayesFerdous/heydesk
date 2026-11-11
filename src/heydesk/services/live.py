"""Tells an open inbox that a chat changed, so it needn't be refreshed.

A page that has the inbox open holds one stream. When a committed change touches a
conversation, the workspace's open pages get `{"conversation_id": ...}` and fetch that chat. With
no page open, nothing happens: no query, no timer.

Each process keeps its own open pages in memory. With Redis, a change is published on one Redis
channel and every process (`run`) hands it to its own pages, so a page hears about a change
whichever process made it. Without Redis (one process, development, tests) it goes straight to
this process's pages.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from heydesk.models import Conversation

log = logging.getLogger(__name__)

QUEUE_SIZE = 100  # a page this far behind loses events; it fetches the whole list again
CHANNEL = "heydesk:live"  # the Redis channel: "<workspace id> <conversation id>"
RETRY_SECONDS = 5  # after Redis drops, how long `run` waits to listen again


class LiveHub:
    def __init__(self, redis: Redis | None = None) -> None:
        self.pages: dict[str, set[asyncio.Queue[str]]] = {}  # open inboxes, by workspace
        self.redis = redis
        self.sending: set[asyncio.Task[None]] = set()  # changes on their way to Redis

    def publish(self, workspace_id: str, conversation_id: str) -> None:
        """A conversation changed. Called after a commit, from SQLAlchemy's event, which can't
        wait: a change for Redis is sent in a task of its own."""
        if self.redis is None:
            self.deliver(workspace_id, conversation_id)
            return
        task = asyncio.get_running_loop().create_task(self._send(workspace_id, conversation_id))
        self.sending.add(task)  # kept, or the task could be dropped before it ran
        task.add_done_callback(self.sending.discard)

    async def _send(self, workspace_id: str, conversation_id: str) -> None:
        assert self.redis is not None
        try:
            await self.redis.publish(CHANNEL, f"{workspace_id} {conversation_id}")
        except RedisError:  # the page shows the change on its next refresh
            log.warning("inbox change not sent to redis", extra={"workspace_id": workspace_id})

    async def run(self) -> None:
        """With Redis: hands every process's changes to this process's open pages. When Redis
        drops, listens again after RETRY_SECONDS; changes made meanwhile are missed."""
        assert self.redis is not None
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            workspace_id, conversation_id = message["data"].decode().split()
                            self.deliver(workspace_id, conversation_id)
            except RedisError:
                log.warning("live inbox lost redis, listening again soon")
                await asyncio.sleep(RETRY_SECONDS)

    def deliver(self, workspace_id: str, conversation_id: str) -> None:
        """Tells this process's open pages of the workspace."""
        for queue in self.pages.get(workspace_id, ()):
            if queue.full():
                queue.get_nowait()  # the oldest goes; the page still hears about the newest
            queue.put_nowait(conversation_id)

    @contextmanager
    def listen(self, workspace_id: str) -> Iterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_SIZE)
        self.pages.setdefault(workspace_id, set()).add(queue)
        try:
            yield queue
        finally:
            pages = self.pages[workspace_id]
            pages.discard(queue)
            if not pages:
                del self.pages[workspace_id]


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
        for workspace_id, conversation_id in session.info.pop("changed", ()):
            hub.publish(workspace_id, conversation_id)

    @event.listens_for(session_class, "after_rollback")
    def forget(session: Session) -> None:
        session.info.pop("changed", None)
