"""Opening hours, shared by every preset's knowledge."""

from datetime import time

from pydantic import BaseModel

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

type Hours = tuple[time, time]  # open, close


class WeeklyHours(BaseModel):
    """`{mon: ["11:00", "22:00"], …}`. A day left out is closed.

    In YAML, quote the times: an unquoted 11:00 is read as a number.
    """

    mon: Hours | None = None
    tue: Hours | None = None
    wed: Hours | None = None
    thu: Hours | None = None
    fri: Hours | None = None
    sat: Hours | None = None
    sun: Hours | None = None

    def render(self) -> str:
        """One entry per day: "Mon 11:00–22:00, Tue 11:00–22:00, …, Sun closed"."""
        parts = []
        for day in DAYS:
            hours: Hours | None = getattr(self, day)
            when = f"{hours[0]:%H:%M}–{hours[1]:%H:%M}" if hours else "closed"
            parts.append(f"{day.title()} {when}")
        return ", ".join(parts)
