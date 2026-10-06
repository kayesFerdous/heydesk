"""What every business edits about its agent (design §5). Each desk adds its own fields."""

from pydantic import BaseModel, ConfigDict, Field

from heydesk.runtime.schedule import WeeklyHours

# The only names customer text may use. Filled with str.replace, never str.format:
# a customer's notes may contain braces.
PLACEHOLDERS = ("business_name",)


class Handoff(BaseModel):
    message: str = "Let me get a member of our team to help you."
    phone: str | None = None
    email: str | None = None


class AgentConfig(BaseModel):
    """The fields every kind of business has. A desk's config subclasses this: the clinic adds
    `emergency_phone`, the restaurant `policies`, and each sets its own `greeting`."""

    model_config = ConfigDict(extra="forbid")  # a field this desk doesn't have is a 422

    name: str
    business_name: str
    timezone: str = "Asia/Dhaka"
    country: str = "BD"  # for phone numbers
    currency: str = "BDT"
    greeting: str = "Hello, thanks for contacting {business_name}. How can I help you today?"
    address: str = ""  # left empty at first: the setup list says what's missing
    hours: WeeklyHours = WeeklyHours()
    notes: str = Field(default="", max_length=4000)  # the owner's: tone, rules, anything to add
    faq: str = Field(default="", max_length=20000)
    handoff: Handoff = Handoff()

    def fill(self, text: str) -> str:
        """Customer text with `{business_name}` and friends filled in."""
        for name in PLACEHOLDERS:
            text = text.replace("{" + name + "}", str(getattr(self, name)))
        return text
