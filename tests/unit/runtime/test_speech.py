import pytest

from heydesk.runtime.speech import money


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
