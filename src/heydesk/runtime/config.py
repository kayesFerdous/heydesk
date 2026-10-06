"""An agent's config: what a business edits (design §5)."""

from typing import Any

from pydantic import BaseModel, Field

# The only names customer text may use. Filled with str.replace, never str.format:
# a customer's instructions may contain braces.
PLACEHOLDERS = ("business_name",)


class Handoff(BaseModel):
    message: str = "Let me get a member of our team to help you."
    phone: str | None = None
    email: str | None = None


class AgentConfig(BaseModel):
    preset: str
    name: str
    business_name: str
    timezone: str = "Asia/Dhaka"
    country: str = "BD"  # for phone numbers
    currency: str = "BDT"
    greeting: str
    instructions: str = Field(max_length=8000)  # the role text: HeyDesk's, from the preset (D25)
    notes: str = Field(default="", max_length=4000)  # the owner's: tone, rules, anything to add
    knowledge: dict[str, Any] = Field(default_factory=dict)  # checked by the preset's model
    faq: str = Field(default="", max_length=20000)
    handoff: Handoff = Handoff()

    def fill(self, text: str) -> str:
        """Customer text with `{business_name}` and friends filled in."""
        for name in PLACEHOLDERS:
            text = text.replace("{" + name + "}", str(getattr(self, name)))
        return text
