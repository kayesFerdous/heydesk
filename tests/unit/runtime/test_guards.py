from heydesk.domain.values import Confirmed
from heydesk.runtime.commit_check import ask_for_yes, confirm

READ_BACK = "2 × Chicken Biryani; pickup today at 7:30 pm; name Rahim; phone 01711000000"


def test_the_read_back_they_heard_confirms_it() -> None:
    assert confirm(READ_BACK, "the order", READ_BACK) == Confirmed("the order")


def test_no_read_back_or_other_details_is_not_confirmed() -> None:
    assert confirm("", "the order", READ_BACK) is None
    assert confirm(READ_BACK, "the order", READ_BACK.replace("2 ×", "3 ×")) is None


def test_asking_for_a_yes_keeps_the_read_back_in_the_state() -> None:
    result = ask_for_yes(READ_BACK, "place_order")

    assert not result.ok
    assert result.state == {"read_back": READ_BACK}
    assert result.choices == ["Yes", "Change"]
