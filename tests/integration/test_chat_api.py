import json

from httpx import AsyncClient


def parse(body: str) -> list[tuple[str, dict[str, object]]]:
    frames = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


async def test_chat_streams_tokens_over_sse(client: AsyncClient) -> None:
    res = await client.post(
        "/api/chat/dev", json={"message": "a table for two", "client_message_id": "m1"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    frames = parse(res.text)
    assert frames[0][0] == "open" and frames[0][1]["conversation_id"]
    assert frames[-1] == ("done", {"interrupted": False})
    text = "".join(str(data["text"]) for event, data in frames if event == "token")
    assert text == "(fake model) You said: a table for two"


async def test_rejects_an_empty_or_long_message_before_streaming(client: AsyncClient) -> None:
    for message in ("   ", "x" * 2001):
        res = await client.post(
            "/api/chat/dev", json={"message": message, "client_message_id": "m1"}
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "bad_request"
