"""What every business edits about its agent (design §5). Each desk adds its own fields."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from heydesk.runtime.schedule import WeeklyHours

# The only names customer text may use. Filled with str.replace, never str.format:
# a customer's notes may contain braces.
PLACEHOLDERS = ("business_name",)

# One of ours, saved by PhotoService: on this server's disk, or in R2 at its public address
LOGO = r"^((/media|https://[\w.-]+)/[\w-]+/logo/[0-9a-f]{32}\.jpg)?$"


class Handoff(BaseModel):
    message: str = "Let me get a member of our team to help you."
    phone: str | None = None
    email: str | None = None
    # a hand-off goes to the joined moderator with the fewest open chats
    auto_assign: bool = False


class Widget(BaseModel):
    """How the chat looks on the business's own website: the bubble and its window."""

    colour: str = Field(default="#0f766e", pattern=r"^#[0-9a-fA-F]{6}$")  # the bubble and header
    side: Literal["right", "left"] = "right"
    # The line that pops out of the bubble after a few seconds; empty: none
    nudge: str = Field(default="Hi! Can I help you?", max_length=120)
    # Tap-to-ask buttons on the welcome screen; each desk has its own
    starters: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=4
    )
    logo: str = Field(default="", pattern=LOGO)  # set by POST /api/agent/logo; empty: initials


class AgentConfig(BaseModel):
    """The fields every kind of business has. A desk's config subclasses this: the shop adds
    `delivery` and `policies`, and sets its own `greeting`."""

    model_config = ConfigDict(extra="forbid")  # a field this desk doesn't have is a 422

    # The owner's master switch: true turns the AI off for every visitor on every channel.
    # Messages wait for the team, as with AI off for one visitor
    ai_off: bool = False
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
    widget: Widget = Widget()  # a desk sets its own starters
    # The team's saved replies, picked from the inbox's reply box; never the model's
    saved_replies: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default=[], max_length=50
    )

    def fill(self, text: str) -> str:
        """Customer text with `{business_name}` and friends filled in."""
        for name in PLACEHOLDERS:
            text = text.replace("{" + name + "}", str(getattr(self, name)))
        return text
