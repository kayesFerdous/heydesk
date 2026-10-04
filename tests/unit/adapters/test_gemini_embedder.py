import json

import httpx
import pytest

from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.embedder.gemini import BATCH, GeminiEmbedder
from heydesk.ports.embedder import EmbeddingUnavailable


def embedder(handler: httpx.MockTransport | None = None, **kw: object) -> GeminiEmbedder:
    e = GeminiEmbedder("secret-key", dimensions=4, **kw)  # type: ignore[arg-type]
    e.client = httpx.AsyncClient(transport=handler)
    return e


def answer(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "embeddings": [{"values": [float(n), 0, 0, 1]} for n, _ in enumerate(body["requests"])]
        },
    )


async def test_documents_and_questions_are_written_the_way_the_model_expects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    e = embedder(httpx.MockTransport(handler))

    docs = await e.embed_documents(["We open at 9."], title="Hours")
    question = await e.embed_query("when do you open?")

    first, second = (json.loads(r.content)["requests"][0] for r in seen)
    assert first["content"]["parts"][0]["text"] == "title: Hours | text: We open at 9."
    assert second["content"]["parts"][0]["text"] == "task: search result | query: when do you open?"
    assert first["outputDimensionality"] == 4 and first["model"] == "models/gemini-embedding-2"
    assert seen[0].headers["x-goog-api-key"] == "secret-key"
    assert str(seen[0].url).endswith("/gemini-embedding-2:batchEmbedContents")
    assert len(docs) == 1 and len(question) == 4


async def test_many_texts_go_in_batches_and_come_back_in_order() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(json.loads(request.content)["requests"]))
        return answer(request)

    vectors = await embedder(httpx.MockTransport(handler)).embed_documents(["x"] * (BATCH + 5))

    assert calls == [BATCH, 5] and len(vectors) == BATCH + 5


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="quota text we must not echo"),
        httpx.Response(200, json={"oops": 1}),
    ],
)
async def test_a_failure_is_one_error_that_never_repeats_the_response(
    response: httpx.Response,
) -> None:
    e = embedder(httpx.MockTransport(lambda request: response))

    with pytest.raises(EmbeddingUnavailable) as caught:
        await e.embed_query("hello")

    assert "quota text" not in str(caught.value) and "secret-key" not in str(caught.value)


async def test_a_timeout_is_the_same_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EmbeddingUnavailable):
        await embedder(httpx.MockTransport(handler)).embed_query("hello")


async def test_the_fake_puts_texts_that_share_words_close_together() -> None:
    fake = FakeEmbedder()
    a, b, c = await fake.embed_documents(["we open at nine", "when do we open", "parking is free"])

    def cos(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True))

    assert cos(a, b) > cos(a, c)
    assert abs(cos(a, a) - 1) < 1e-9
