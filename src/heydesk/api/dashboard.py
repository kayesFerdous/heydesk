"""The owner's dashboard: chats, sales and call backs, by the business's own days."""

from dataclasses import asdict
from datetime import datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel

from heydesk.api.deps import Svc, WorkspaceId

router = APIRouter(tags=["dashboard"])


class StatsOut(BaseModel):
    conversations: int  # chats the agent answered
    handed_off: int  # of those, handed to a person; the rest the agent handled alone
    sales: int  # orders, not cancelled or waiting for payment
    amount: int  # their total, minor units
    bought: int  # chats that ended in one
    sources: dict[str, int]  # chat, page or team
    leads: int  # call backs asked for and not sold


class DayOut(BaseModel):
    start: datetime  # when the day began where the business is
    sales: int
    amount: int  # minor units


class DashboardOut(BaseModel):
    days: int
    currency: str
    today: StatsOut
    yesterday: StatsOut
    period: StatsOut  # the last `days` days, today included
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

    async def between(start: datetime, end: datetime) -> StatsOut:
        return StatsOut(**asdict(await store.stats(start, end)))

    first = today - (days - 1) * day
    sold = await store.sales_between(first, today + day)

    def day_out(start: datetime) -> DayOut:
        totals = [total for at, total in sold if start <= at < start + day]
        return DayOut(start=start, sales=len(totals), amount=sum(totals))

    return DashboardOut(
        days=days,
        currency=agent.config.currency,
        today=await between(today, today + day),
        yesterday=await between(today - day, today),
        period=await between(first, today + day),
        daily=[day_out(first + back * day) for back in range(days)],
    )
