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
    assert agent.greeting.startswith("Hi, welcome to Spice Garden!")
    assert agent.desk.kind == "restaurant"


async def test_a_restart_updates_the_same_agent(world: World, tmp_path: Path) -> None:
    edited = tmp_path / "restaurant.yaml"
    edited.write_text(SEED.read_text().replace("2 minutes away", "3 minutes away"))

    agent = await world.services.agents.ensure_dev_agent("restaurant", edited)

    assert "3 minutes away" in agent.config.faq
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


async def test_changing_the_dev_agents_kind_keeps_the_one_agent(world: World) -> None:
    agent = await world.services.agents.ensure_dev_agent(
        "receptionist", SEED.parent / "receptionist.yaml"
    )

    assert (
        agent.greeting
        == "Hello, thanks for contacting Rahman Accounting. How can I help you today?"
    )
    assert (agent.desk.kind, agent.public_key) == ("receptionist", "dev-receptionist")
    assert [await count(world, m) for m in (Workspace, Agent)] == [1, 1]
