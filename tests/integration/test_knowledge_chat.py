"""A document an owner adds is found by the agent in a chat, and only for that business."""

import re

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from heydesk.shop import tools_for
from tests.conftest import DEV_KEY, new_business

POLICY = "Refunds are given within seven days with the receipt. Parking is free in front."
ANSWER = "Refunds are possible within seven days if you have the receipt."


async def chat(app: FastAPI, client: AsyncClient, key: str, text: str) -> str:
    response = await client.post(
        f"/api/chat/{key}", json={"visitor_id": "test-visitor", "message": text}
    )
    return response.text


async def test_the_agent_looks_in_the_documents_and_answers_from_them(
    app: FastAPI, client: AsyncClient
) -> None:
    await client.post("/api/agent/documents", json={"title": "Policies", "text": POLICY})
    llm = app.state.services.conversations.runtime.llm
    llm.script = [
        ToolCall("c1", "search_knowledge", {"question": "refund after seven days"}),
        ANSWER,
    ]

    body = await chat(app, client, DEV_KEY, "can I get a refund?")

    assert "".join(re.findall(r'"text": "([^"]*)"', body)).strip() == ANSWER  # streamed as tokens
    assert "Checking our documents" in body  # the visitor sees why it's waiting
    request = llm.requests[0]
    assert "search_knowledge" in [t.name for t in request.tools]
    assert "# Documents\nThe business has added documents: Policies." in request.system
    tool_result = llm.requests[1].messages[-1]["content"]  # what the model read back
    assert "within seven days with the receipt" in tool_result
    assert '"source": "Policies"' in tool_result


async def test_without_documents_there_is_no_search_tool_and_no_documents_section(
    app: FastAPI, client: AsyncClient
) -> None:
    llm = app.state.services.conversations.runtime.llm
    llm.script = ["Hello!"]

    await chat(app, client, DEV_KEY, "hi")

    assert "search_knowledge" not in [t.name for t in llm.requests[0].tools]
    assert "# Documents" not in llm.requests[0].system


async def test_another_business_never_gets_the_documents(app: FastAPI, client: AsyncClient) -> None:
    await client.post("/api/agent/documents", json={"title": "Secret", "text": POLICY})
    other = await new_business(app)
    try:
        agent = (await other.post("/api/agent", json={})).json()
        live = await app.state.services.agents.by_key(agent["public_key"])
        assert live.documents == []
        assert "search_knowledge" not in {
            t.name for t in tools_for(has_documents=bool(live.documents))
        }
        # Even asked directly, the other business's search finds nothing of the first's
        found = await app.state.services.knowledge.search(live.workspace_id, "refunds seven days")
        assert found == []
    finally:
        await other.aclose()


async def test_deleting_the_last_document_takes_the_tool_away(
    app: FastAPI, client: AsyncClient
) -> None:
    doc = (await client.post("/api/agent/documents", json={"title": "P", "text": POLICY})).json()
    assert (await app.state.services.agents.by_key(DEV_KEY)).documents == ["P"]

    await client.delete(f"/api/agent/documents/{doc['id']}")

    assert (await app.state.services.agents.by_key(DEV_KEY)).documents == []


async def test_a_missed_question_is_listed_taught_and_found_next_time(
    app: FastAPI, client: AsyncClient
) -> None:
    """A hand-off for a question it didn't know is saved; two asked alike are one row with a
    count; *Teach it* makes a knowledge entry that the search finds."""
    llm = app.state.services.conversations.runtime.llm
    for asked in ("Do you cater weddings?", "do you cater a wedding"):
        llm.script = [
            ToolCall("c1", "handoff_to_human", {"unanswered": asked}),
            "Let me pass you to the team.",
        ]
        await chat(app, client, DEV_KEY, asked)
        app.state.services.conversations.now.advance(minutes=1)
    [group] = (await client.get("/api/agent/missed")).json()
    assert (group["question"], group["count"]) == ("Do you cater weddings?", 2)

    taught = await client.post(
        "/api/agent/missed/teach",
        json={"ids": group["ids"], "question": group["question"], "answer": "Yes, up to 200."},
    )
    assert taught.status_code == 201
    assert (await client.get("/api/agent/missed")).json() == []
    llm.script = [ToolCall("c1", "search_knowledge", {"question": "cater weddings"}), "Yes!"]
    await chat(app, client, DEV_KEY, "weddings?")
    assert "up to 200" in llm.requests[-1].messages[-1]["content"]
