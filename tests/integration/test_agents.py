from pathlib import Path

import pytest
from sqlalchemy import func, select

from heydesk.errors import NotFound
from heydesk.models import Agent, Workspace
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY, SEED, World


async def count(world: World, model: type) -> int:
    async with world.sessions() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


async def test_the_dev_agent_comes_from_its_seed(world: World) -> None:
    agent = await world.services.agents.by_key(DEV_KEY)

    assert agent.workspace_id == DEV_WORKSPACE_ID
    assert agent.greeting.startswith("Assalamu alaikum! Welcome to Nakshi Threads.")


async def test_a_restart_updates_the_same_agent(world: World, tmp_path: Path) -> None:
    edited = tmp_path / "shop.yaml"
    edited.write_text(SEED.read_text().replace("before 5 pm", "before 6 pm"))

    agent = await world.services.agents.ensure_dev_agent(edited)

    assert "before 6 pm" in agent.config.faq
    assert [await count(world, m) for m in (Workspace, Agent)] == [1, 1]


async def test_an_edit_is_live_at_once(world: World) -> None:

    await world.services.agents.update(DEV_WORKSPACE_ID, {"faq": "New."})

    assert (await world.services.agents.by_key(DEV_KEY)).config.faq == "New."


async def test_an_unknown_key_is_not_found(world: World) -> None:
    with pytest.raises(NotFound):
        await world.services.agents.by_key("nope")


async def test_another_workspace_cant_edit_the_agent(world: World) -> None:
    with pytest.raises(NotFound):
        await world.services.agents.update("someone-else", {"faq": "Mine."})
