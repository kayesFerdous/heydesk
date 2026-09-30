from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from heydesk.api.deps import get_services
from heydesk.api.sse import to_sse
from heydesk.services import Services

router = APIRouter()


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None
    client_message_id: str  # a uuid per message, reused on retry (D10)


@router.post("/api/chat/{public_key}")
async def chat(
    public_key: str, body: ChatIn, services: Annotated[Services, Depends(get_services)]
) -> StreamingResponse:
    turn = await services.conversations.start_turn(
        public_key, body.conversation_id, body.message, body.client_message_id, channel="web"
    )
    return StreamingResponse(
        to_sse(turn),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
