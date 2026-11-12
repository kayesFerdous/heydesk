"""The owner's dashboard: chats, sales and call backs, by the business's own days."""

from datetime import datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.shop.store import Stats

router = APIRouter(tags=["dashboard"])


class DayOut(BaseModel):
    start: datetime  # when the day began where the business is
    sales: int
    amount: int  # minor units


class DashboardOut(BaseModel):
    days: int
    currency: str
    today: Stats
    yesterday: Stats
    period: Stats  # the last `days` days, today included
    daily: list[DayOut]  # those days one by one, oldest first and today last


@router.get("/api/stats")
async def stats(
    services: Svc, workspace_id: WorkspaceId, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> DashboardOut:
    """The owner's dashboard: today, yesterday and the last `days` days, in the
    business's own days, with the sales of each of those days."""
    agent = await services.agents.by_workspace(workspace_id)
    store = await services.agents.store(workspace_id)
    zone = ZoneInfo(agent.config.timezone)
    today = datetime.combine(store.now().astimezone(zone).date(), time(), zone)
    day = timedelta(days=1)

    first = today - (days - 1) * day
    sold = await store.sales_between(first, today + day)

    def day_out(start: datetime) -> DayOut:
        totals = [total for at, total in sold if start <= at < start + day]
        return DayOut(start=start, sales=len(totals), amount=sum(totals))

    return DashboardOut(
        days=days,
        currency=agent.config.currency,
        today=await store.stats(today, today + day),
        yesterday=await store.stats(today - day, today),
        period=await store.stats(first, today + day),
        daily=[day_out(first + back * day) for back in range(days)],
    )
