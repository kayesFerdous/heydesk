from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    async with request.app.state.sessions() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}
