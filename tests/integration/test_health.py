import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from heydesk.main import create_app
from tests.conftest import make_test_settings


async def test_health_is_200(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "commit": ""}  # Render sets the commit; tests don't


async def test_a_request_logs_one_json_line_with_its_id(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(make_test_settings())  # built here so its log handler writes to capsys
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
    ):
        capsys.readouterr()
        r = await c.get("/health")

    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    requests = [line for line in lines if line["logger"] == "heydesk.request"]
    assert len(requests) == 1
    assert requests[0]["request_id"] == r.headers["x-request-id"]
    assert requests[0]["path"] == "/health"
    assert requests[0]["status"] == 200
