from heydesk.adapters.decider.fake import FakeDecider
from heydesk.domain.values import Confirmed
from heydesk.ports.decider import DeciderUnavailable
from heydesk.runtime.guards import confirm

READ_BACK = "2 × Chicken Biryani, total ৳900, for 7:30 pm. Shall I place it?"


async def check(decider: FakeDecider, reply: str = "yes") -> Confirmed[str] | None:
    return await confirm(decider, "the order", "items = 2 × Chicken Biryani", READ_BACK, reply)


async def test_a_read_back_and_a_yes_confirm_it() -> None:
    decider = FakeDecider([{"read_back": 0.95, "said_yes": 0.9}])

    assert await check(decider) == Confirmed("the order")
    assert decider.asked == [
        {
            "details_to_confirm": "items = 2 × Chicken Biryani",
            "agent_last_message": READ_BACK,
            "visitor_reply": "yes",
        }
    ]


async def test_anything_less_is_not_confirmed() -> None:
    maybe = FakeDecider([{"read_back": 0.95, "said_yes": 0.4}])
    assert await check(maybe, "hmm, maybe") is None
    no_read_back = FakeDecider([{"read_back": 0.2, "said_yes": 0.95}])
    assert await check(no_read_back) is None


async def test_it_fails_closed_when_jev_is_down() -> None:
    assert await check(FakeDecider([DeciderUnavailable("timeout")])) is None
