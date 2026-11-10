import asyncio

from heydesk.services.background import run_every


async def test_a_failing_round_does_not_stop_the_job() -> None:
    rounds = 0
    third = asyncio.Event()

    async def job() -> None:
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            raise RuntimeError("the database blinked")
        if rounds == 3:
            third.set()

    task = asyncio.create_task(run_every(0.01, job))
    await asyncio.wait_for(third.wait(), timeout=2)
    task.cancel()
    assert rounds == 3
