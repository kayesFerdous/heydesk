"""The minimal chat page for the `.env` agent (dev only; retired when the Vue app lands)."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

WEB_STATIC = Path(__file__).parent.parent / "web" / "static"

router = APIRouter(include_in_schema=False)


@router.get("/")
async def chat_page(request: Request) -> HTMLResponse:
    """index.html with the dev agent's public key baked in (design §5)."""
    page = await asyncio.to_thread((WEB_STATIC / "index.html").read_text, encoding="utf-8")
    return HTMLResponse(page.replace("__AGENT_KEY__", request.app.state.dev_agent_key))
