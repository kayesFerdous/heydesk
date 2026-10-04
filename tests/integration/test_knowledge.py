"""The knowledge service: nothing half-saved, words-only when the embedder is down, rebuildable."""

import pytest
from sqlalchemy import func, select

from heydesk.errors import KnowledgeUnavailable, UnreadableDocument
from heydesk.models import Document
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.knowledge import MAX_CHARS, MAX_DOCUMENTS
from tests.conftest import DEV_KEY, World

HOURS = "We open at nine in the morning and close at ten at night, Saturday to Thursday."
FEES = "A general visit costs 400 taka. Dr. Hasan charges 900 taka."


async def agent_id(world: World) -> str:
    return (await world.services.agents.by_key(DEV_KEY)).agent_id


async def add(world: World, title: str, text: str) -> Document:
    return await world.services.knowledge.add(DEV_WORKSPACE_ID, await agent_id(world), title, text)


async def count(world: World) -> int:
    async with world.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(Document)) or 0)


async def test_an_added_document_is_found_by_a_question(world: World) -> None:
    await add(world, "Hours", HOURS)
    await add(world, "Fees", FEES)

    found = await world.services.knowledge.search(DEV_WORKSPACE_ID, "what time do you open")

    assert found[0].title == "Hours" and "nine" in found[0].text
    assert await world.services.knowledge.titles(await agent_id(world)) == ["Hours", "Fees"]


async def test_if_the_embedder_is_down_nothing_is_saved(world: World) -> None:
    world.embedder.fail_documents = True

    with pytest.raises(KnowledgeUnavailable):
        await add(world, "Hours", HOURS)

    assert await count(world) == 0
    world.embedder.fail_documents = False
    assert await world.services.knowledge.search(DEV_WORKSPACE_ID, "nine") == []  # no chunks either


async def test_if_the_question_cant_be_embedded_the_words_still_find_it(world: World) -> None:
    await add(world, "Fees", FEES)
    world.embedder.fail_queries = True

    found = await world.services.knowledge.search(DEV_WORKSPACE_ID, "Hasan")

    assert found and "Dr. Hasan charges 900 taka." in found[0].text


async def test_limits_are_enforced_before_anything_is_embedded(world: World) -> None:
    with pytest.raises(UnreadableDocument, match="empty"):
        await add(world, "x", "  ")
    with pytest.raises(UnreadableDocument, match="characters"):
        await add(world, "x", "a " * MAX_CHARS)
    for n in range(MAX_DOCUMENTS):
        await add(world, f"Doc {n}", f"Fact number {n}.")

    with pytest.raises(UnreadableDocument, match="30 documents"):
        await add(world, "One too many", "More.")
    assert await count(world) == MAX_DOCUMENTS


async def test_deleting_removes_the_text_and_the_chunks(world: World) -> None:
    doc = await add(world, "Hours", HOURS)

    await world.services.knowledge.delete(DEV_WORKSPACE_ID, doc.id)

    assert await count(world) == 0
    assert await world.services.knowledge.search(DEV_WORKSPACE_ID, "nine") == []


async def test_reindex_rebuilds_the_chunks_from_the_saved_text(world: World) -> None:
    await add(world, "Hours", HOURS)
    store = world.services.knowledge.store
    await store.delete_document(  # the index is lost, as if the collection was recreated
        DEV_WORKSPACE_ID, (await world.services.knowledge.all(DEV_WORKSPACE_ID))[0].id
    )
    assert await world.services.knowledge.search(DEV_WORKSPACE_ID, "nine") == []

    rebuilt = await world.services.knowledge.reindex()

    assert rebuilt == 1
    assert (await world.services.knowledge.search(DEV_WORKSPACE_ID, "nine"))[0].title == "Hours"
