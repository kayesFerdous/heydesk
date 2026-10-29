"""The inbox hears about a chat the moment it changes (D158)."""

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.api.sse import live_frames
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY


async def test_an_open_inbox_hears_a_chat_that_changed(app: FastAPI, client: AsyncClient) -> None:
    app.state.services.conversations.runtime.llm.script = ["Hello!"]
    hub = app.state.services.live
    frames = live_frames(hub, DEV_WORKSPACE_ID)
    assert (await anext(frames)).startswith("event: open")  # listening now

    res = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "hi"}
    )
    conv = res.text.split('"conversation_id": "')[1].split('"')[0]
    assert f'"conversation_id": "{conv}"' in await anext(frames)  # the visitor's turn

    await client.put(f"/api/conversations/{conv}/labels", json={"labels": ["vip"]})
    assert f'"conversation_id": "{conv}"' in await anext(frames)  # the team's change

    await frames.aclose()
    assert hub.pages == {}  # closed, so nothing is kept for it


async def test_another_business_hears_nothing(app: FastAPI, client: AsyncClient) -> None:
    with app.state.services.live.listen("another-business") as queue:  # per business (rule 6)
        await client.post(
            f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "hi"}
        )
        assert queue.empty()
