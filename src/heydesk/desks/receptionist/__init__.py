"""The receptionist: answers questions about the business and takes messages for the team.
It has no tables or tools of its own: messages and hand-off are shared by every desk."""

from typing import ClassVar

from pydantic import Field

from heydesk.desks.base import Desk, Store, missing_basics
from heydesk.desks.shared import returning
from heydesk.models import TeamMessage
from heydesk.runtime.config import AgentConfig, Widget
from heydesk.runtime.tool import Tool

ROLE = """\
# Your role
You are the receptionist for {business_name}.
Be friendly, calm and brief: two or three short sentences per reply.
Answer questions from what you know about the business below. When you don't know, say so
and offer to take a message for the team.
You can't book appointments or quote for custom work: take a message instead with
take_message. Ask for the message first. Then ask once if they'd like to leave a name and
phone so the team can call back; that's optional, and a no is fine."""


class ReceptionistConfig(AgentConfig):
    widget: Widget = Widget(starters=["What do you do?", "When are you open?", "Leave a message"])
    about: str = Field(default="", max_length=4000)  # what the business does, who's on the team
    services: str = Field(default="", max_length=8000)  # what it offers, with prices if any


class ReceptionistDesk(Desk):
    kind = "receptionist"
    label = "Receptionist"
    description = "Answers questions about the business and takes messages for the team."
    config_model = ReceptionistConfig
    tools: ClassVar[list[Tool]] = []  # only the shared ones
    takes_voice = True  # a visitor can call it

    async def prompt(self, config: ReceptionistConfig, store: Store) -> str:
        about = ["# The business"]
        if config.address.strip():
            about.append(f"Address: {config.address.strip()}")
        if config.hours.is_set():
            about.append(f"Office hours: {config.hours.render()}")
        if config.about.strip():
            about += ["About us:", config.about.strip()]
        if config.services.strip():
            about += ["Services:", config.services.strip()]
        sections = [ROLE, "\n".join(about)]
        for last in await store.before(TeamMessage):  # back again, if they left a name
            if last.name or last.phone:
                facts = [f"Their last message {last.code}: name {last.name}; phone {last.phone}"]
                sections.append(returning("Their last message", facts))
        return "\n\n".join(sections)

    async def missing(self, config: ReceptionistConfig, store: Store) -> list[str]:
        missing = missing_basics(config)
        if not config.services.strip():
            missing.append("Add your services.")
        return missing


RECEPTIONIST = ReceptionistDesk()
