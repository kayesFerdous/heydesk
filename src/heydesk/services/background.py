"""The jobs the app runs on its own, beside the requests: syncing sheets, Woo stores and
couriers, ringing due calls, waking snoozed chats. The app's lifespan starts each one with
`run_every`, and cancels them all when it stops."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

type Job = Callable[[], Awaitable[None]]


async def run_every(seconds: float, job: Job) -> None:
    """Runs `job` every `seconds`, until cancelled. A round that fails is logged and the next
    one runs as usual: a task nobody awaits would otherwise die without a word."""
    while True:
        await asyncio.sleep(seconds)
        await _run(job)


async def run_once_after(seconds: float, job: Job) -> None:
    """Runs `job` once, `seconds` after the start."""
    await asyncio.sleep(seconds)
    await _run(job)


async def _run(job: Job) -> None:
    try:
        await job()
    except Exception:
        log.exception("background job failed", extra={"job": job.__qualname__})
