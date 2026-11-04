"""Opening hours."""

from datetime import date, datetime, time

from pydantic import BaseModel

from heydesk.runtime.speech import clock_time

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

type Hours = tuple[time, time]  # open, close


class WeeklyHours(BaseModel):
    """`{mon: ["11:00", "22:00"], …}`. A day left out is closed.

    No days given at all means the hours aren't set yet, and nothing is checked. Days given
    but every one closed means closed all week: everything is refused (the editor saves all
    seven days, so "every day off" is stored as seven nulls).
    In YAML, quote the times: an unquoted 11:00 is read as a number.
    """

    mon: Hours | None = None
    tue: Hours | None = None
    wed: Hours | None = None
    thu: Hours | None = None
    fri: Hours | None = None
    sat: Hours | None = None
    sun: Hours | None = None

    def is_set(self) -> bool:
        """False until the owner has given any day, open or closed."""
        return bool(self.model_fields_set)

    def on(self, day: date) -> Hours | None:
        """That day's hours, or None if it's closed."""
        hours: Hours | None = getattr(self, DAYS[day.weekday()])
        return hours

    def problem_with(self, when: datetime) -> str | None:
        """Why `when` (local time) is outside the hours, in words for the model; None if it's
        inside, or no hours are set yet. Open is inclusive, close is not."""
        if not self.is_set():
            return None
        hours = self.on(when.date())
        day = when.strftime("%A")
        if hours is None:
            return f"We're closed on {day}s. Our hours: {self.render()}. Ask for another day."
        if not hours[0] <= when.time() < hours[1]:
            return (
                f"On {day}s we're open {hours[0]:%H:%M}–{hours[1]:%H:%M}, so "
                f"{clock_time(when.time())} is outside our hours. Ask for a time within them."
            )
        return None

    def render(self) -> str:
        """One entry per day: "Mon 11:00–22:00, Tue 11:00–22:00, …, Sun closed"."""
        parts = []
        for day in DAYS:
            hours: Hours | None = getattr(self, day)
            when = f"{hours[0]:%H:%M}–{hours[1]:%H:%M}" if hours else "closed"
            parts.append(f"{day.title()} {when}")
        return ", ".join(parts)
