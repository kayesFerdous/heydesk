from datetime import time

import pytest

from heydesk.runtime.speech import clock_time, money


@pytest.mark.parametrize(
    ("minor", "currency", "shown"),
    [
        (45000, "BDT", "৳450"),
        (103000, "BDT", "৳1,030"),
        (104550, "BDT", "৳1,045.50"),
        (5, "USD", "$0.05"),
        (1000, "XYZ", "XYZ 10"),
    ],
)
def test_money_display(minor: int, currency: str, shown: str) -> None:
    assert money(minor, currency) == shown


def test_clock_time_display() -> None:
    assert clock_time(time(19, 30)) == "7:30 pm"
    assert clock_time(time(12, 5)) == "12:05 pm"
    assert clock_time(time(0, 15)) == "12:15 am"
