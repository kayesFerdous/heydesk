"""The minimal chat page for the `.env` agent (dev only; retired when the Vue app lands)."""

import asyncio
import html
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

WEB_STATIC = Path(__file__).parent.parent / "web" / "static"

router = APIRouter(include_in_schema=False)


@router.get("/")
async def chat_page(request: Request) -> HTMLResponse:
    """index.html with an agent's public key baked in: `?key=…`, else the `.env` agent's."""
    key = request.query_params.get("key") or request.app.state.dev_agent_key
    page = await asyncio.to_thread((WEB_STATIC / "index.html").read_text, encoding="utf-8")
    return HTMLResponse(page.replace("__AGENT_KEY__", html.escape(key, quote=True)))


@router.get("/phone")
async def phone_page() -> HTMLResponse:
    """A web page that acts as the buyer's phone for the shop's confirmation call."""
    page = await asyncio.to_thread((WEB_STATIC / "phone.html").read_text, encoding="utf-8")
    return HTMLResponse(page)
