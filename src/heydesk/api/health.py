from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    async with request.app.state.sessions() as session:
        await session.execute(text("SELECT 1"))
    # The commit tells CI's deploy job that the new version is the one answering
    return {"status": "ok", "commit": request.app.state.settings.RENDER_GIT_COMMIT}
