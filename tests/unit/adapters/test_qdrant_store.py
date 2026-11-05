"""The Qdrant store, against Qdrant's own engine in memory: real hybrid search, no network."""

from collections.abc import AsyncIterator

import pytest
from qdrant_client import AsyncQdrantClient

from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.knowledge.qdrant import QdrantKnowledgeStore, sparse_vector
from heydesk.ports.knowledge_store import ChunkIn

EMBED = FakeEmbedder(dimensions=64)


@pytest.fixture
async def store() -> AsyncIterator[QdrantKnowledgeStore]:
    s = QdrantKnowledgeStore(AsyncQdrantClient(":memory:"), "chunks", dimensions=64)
    await s.setup()
    yield s
    await s.client.close()


async def add(
    store: QdrantKnowledgeStore, workspace_id: str, doc: str, title: str, *texts: str
) -> None:
    vectors = await EMBED.embed_documents(list(texts))
    chunks = [ChunkIn(n, t, v) for n, (t, v) in enumerate(zip(texts, vectors, strict=True))]
    await store.add(workspace_id, doc, title, chunks)


async def ask(
    store: QdrantKnowledgeStore, workspace_id: str, question: str, limit: int = 3
) -> list[str]:
    vector = await EMBED.embed_query(question)
    return [s.text for s in await store.search(workspace_id, question, vector, limit)]


async def test_a_question_finds_the_chunk_that_answers_it(store: QdrantKnowledgeStore) -> None:
    await add(
        store,
        "w1",
        "d1",
        "Policies",
        "Refunds are given within seven days with the receipt.",
        "Parking is free in front of the clinic.",
        "Blood tests are done between eight and twelve.",
    )

    found = await store.search(
        "w1", "is parking free", await EMBED.embed_query("is parking free"), 2
    )

    assert found[0].text == "Parking is free in front of the clinic."
    assert (found[0].document_id, found[0].title) == ("d1", "Policies")


async def test_the_exact_word_is_found_by_the_sparse_side_even_when_the_vector_is_far(
    store: QdrantKnowledgeStore,
) -> None:
    await add(
        store, "w1", "d1", "Fees", "Dr. Hasan charges 900 taka.", "General visit costs 400 taka."
    )
    far = [0.0] * 63 + [1.0]  # a vector that means nothing here

    found = await store.search("w1", "Hasan", far, 1)  # the dense leg is noise; the word decides

    assert found[0].text == "Dr. Hasan charges 900 taka."


async def test_no_vector_means_words_only(store: QdrantKnowledgeStore) -> None:
    await add(
        store, "w1", "d1", "Fees", "Dr. Hasan charges 900 taka.", "General visit costs 400 taka."
    )

    found = await store.search("w1", "general visit", None, 3)  # the embedder is down

    assert found and found[0].text == "General visit costs 400 taka."
    assert await store.search("w1", "!!!", None, 3) == []  # nothing to match at all


async def test_a_business_never_sees_anothers_chunks(store: QdrantKnowledgeStore) -> None:
    await add(store, "w1", "d1", "Mine", "The secret menu has lamb.")
    await add(store, "w2", "d2", "Theirs", "Our parking is at the back.")

    assert await ask(store, "w2", "secret menu lamb") == ["Our parking is at the back."]
    assert await ask(store, "w1", "secret menu lamb") == ["The secret menu has lamb."]
    assert await ask(store, "nobody", "secret menu lamb") == []


async def test_deleting_a_document_removes_its_chunks_only_for_that_business(
    store: QdrantKnowledgeStore,
) -> None:
    await add(store, "w1", "d1", "A", "Open on Fridays after noon.")
    await add(store, "w1", "d2", "B", "Closed on Eid.")
    await add(store, "w2", "d1", "Same id, other business", "Open on Fridays after noon too.")

    await store.delete_document("w1", "d1")

    assert await ask(store, "w1", "Fridays noon") == [
        "Closed on Eid."
    ] or "Open on Fridays after noon." not in await ask(store, "w1", "Fridays noon")
    assert await ask(store, "w2", "Fridays noon") == ["Open on Fridays after noon too."]


async def test_adding_a_document_again_replaces_its_chunks_not_doubles_them(
    store: QdrantKnowledgeStore,
) -> None:
    await add(store, "w1", "d1", "A", "We open at nine.")
    await add(store, "w1", "d1", "A", "We open at nine.")  # the same ids: an upsert

    assert await ask(store, "w1", "open nine", limit=5) == ["We open at nine."]


async def test_a_dense_floor_drops_chunks_that_are_not_similar_at_all() -> None:
    s = QdrantKnowledgeStore(AsyncQdrantClient(":memory:"), "c", dimensions=64, dense_min_score=0.5)
    await s.setup()
    await add(s, "w1", "d1", "A", "We open at nine in the morning.")

    near = await s.search(
        "w1", "open nine morning", await EMBED.embed_query("open nine morning"), 3
    )
    unrelated = await s.search("w1", "zzz", await EMBED.embed_query("zzz"), 3)

    assert len(near) == 1 and unrelated == []


def test_sparse_vectors_weigh_a_repeated_word_a_little_more_and_a_query_flat() -> None:
    chunk = sparse_vector("taka taka taka fee")
    query = sparse_vector("taka fee", query=True)

    once, thrice = sorted(chunk.values)  # "fee" once, "taka" three times
    assert once < thrice < 2.2  # more mentions weigh more, but saturate
    assert query.values == [1.0, 1.0] and len(query.indices) == 2


async def test_common_words_alone_match_nothing(store: QdrantKnowledgeStore) -> None:
    await add(store, "w1", "d1", "Guide", "The clinic is open at nine and the fee is 400 taka.")

    assert await store.search("w1", "write me a poem about the moon", None, 3) == []  # words only
    assert await store.search("w1", "what is the fee", None, 3)  # "fee" is a real word
    assert sparse_vector("is the of", query=True).indices == []
