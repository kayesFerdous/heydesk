import json

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY


def parse(body: str) -> list[tuple[str, dict[str, object]]]:
    frames = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


async def test_the_page_gets_the_agents_name_and_greeting(client: AsyncClient) -> None:
    res = await client.get(f"/api/chat/{DEV_KEY}")
    assert res.status_code == 200
    info = res.json()
    del info["widget"]  # the website chat's look: tests in test_agents_api.py
    assert info == {
        "name": "Spice Garden assistant",
        "greeting": "Hi, welcome to Spice Garden! Hungry? I can help with the menu or take your order.",
        "photos": False,  # only a shop takes photos
        "voice": False,  # only a receptionist or a clinic takes calls
    }


async def test_chat_streams_tokens_over_sse(client: AsyncClient) -> None:
    res = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "a table for two"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    frames = parse(res.text)
    assert frames[0][0] == "open" and frames[0][1]["conversation_id"]
    assert frames[-1] == ("done", {"interrupted": False})
    text = "".join(str(data["text"]) for event, data in frames if event == "token")
    assert text == "(fake model) You said: a table for two"


async def test_a_tool_shows_a_status_line_and_a_hand_off_shows_the_card(
    app: FastAPI, client: AsyncClient
) -> None:
    llm = app.state.services.conversations.runtime.llm
    llm.script = [ToolCall("call_1", "handoff_to_human", {}), "Of course."]
    res = await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "can I talk to a person?"},
    )

    events = [event for event, _ in parse(res.text)]
    order = list(dict.fromkeys(events))  # each kind once, in the order it first came
    assert order == ["open", "status", "token", "handoff", "done"]  # the status comes first
    frames = dict(parse(res.text))
    assert frames["status"] == {"label": "Getting someone from the team…"}
    assert frames["handoff"]["phone"] == "+880 1700 000000"


async def test_the_next_message_continues_the_conversation(client: AsyncClient) -> None:
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "hi"}
    )
    conversation_id = parse(first.text)[0][1]["conversation_id"]

    res = await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "again", "conversation_id": conversation_id},
    )
    assert parse(res.text)[-1] == ("done", {"interrupted": False})


async def test_an_unknown_agent_is_404(client: AsyncClient) -> None:
    for res in (
        await client.get("/api/chat/nope"),
        await client.post("/api/chat/nope", json={"visitor_id": "test-visitor", "message": "hi"}),
    ):
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"


async def test_rejects_an_empty_or_long_message_before_streaming(client: AsyncClient) -> None:
    for message in ("   ", "x" * 2001):
        res = await client.post(
            f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": message}
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "bad_request"


async def test_a_read_back_sends_its_card_and_the_yes_buttons(
    app: FastAPI, client: AsyncClient
) -> None:
    llm = app.state.services.conversations.runtime.llm
    llm.script = [
        ToolCall("c1", "update_order", {"lines": [{"code": "chk-biryani", "quantity": 2}]}),
        ToolCall("c2", "place_order", {"time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}),
        "2 Chicken Biryani, ৳900, at 7:30 pm. Shall I place it?",
    ]
    res = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "two biryanis"}
    )

    frames = parse(res.text)
    events = [event for event, _ in frames]
    assert events[-2:] == ["choices", "done"]  # the buttons come with the end of the reply
    card = dict(frames)["card"]
    assert card["kind"] == "summary"
    assert card["data"]["total"] == {"minor": 90000, "currency": "BDT", "text": "৳900"}
    assert dict(frames)["choices"] == {"items": ["Yes", "Change"]}
