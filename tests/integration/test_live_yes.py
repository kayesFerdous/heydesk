"""The commit check on how Bangladeshi buyers really say yes (D92): live, against Jev.

`uv run pytest -m live tests/integration/test_live_yes.py` costs Jev quota: ask first."""

import pytest

from heydesk.config import Settings
from heydesk.runtime.guards import confirm
from heydesk.wiring import build_decider

pytestmark = pytest.mark.live

DETAILS = (
    "1 × Cotton Kurti (M, Black), subtotal ৳950; delivery Inside Dhaka ৳60; total ৳1,010, "
    "paid in cash on delivery; to House 12, Road 5, Mirpur 10, Dhaka; name Kayes; "
    "mobile 01712345678"
)
READ_BACK = (
    "Apnar order:\n1 × Cotton Kurti (M, Black) ৳950\nDelivery Inside Dhaka ৳60\nTotal ৳1,010, "
    "cash on delivery\nHouse 12, Road 5, Mirpur 10, Dhaka\nKayes, 01712345678\nConfirm korbo?"
)


@pytest.mark.parametrize("said", ["ji", "ok", "thik ache", "confirm", "ji confirm koren"])
async def test_a_banglish_yes_is_a_yes(said: str) -> None:
    decider = build_decider(Settings(DECIDER="jev"))
    assert await confirm(decider, "order", DETAILS, READ_BACK, said) is not None


@pytest.mark.parametrize("said", ["dekhi", "pore janabo", "dam ta ektu komano jay?", "na"])
async def test_a_maybe_or_a_no_is_not(said: str) -> None:
    decider = build_decider(Settings(DECIDER="jev"))
    assert await confirm(decider, "order", DETAILS, READ_BACK, said) is None
