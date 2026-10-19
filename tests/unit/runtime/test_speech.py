from datetime import time

import pytest

from heydesk.runtime.speech import clock_time, money, speakable


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


@pytest.mark.parametrize(
    ("said", "spoken"),
    [
        ("Total ৳1,030.", "Total one thousand and thirty taka."),
        ("That's ৳1,045.50", "That's one thousand and forty-five taka fifty poisha"),
        ("today, 7:30 pm", "today, seven thirty p.m."),
        ("at 4:05 PM", "at four oh five p.m."),
        ("by 7:00", "by seven o'clock"),
        ("2 × Borhani", "two Borhani"),
        ("Call 01712-345678", "Call zero one seven one two three four five six seven eight"),
        ("Order #101 is in.", "Order number one hundred and one is in."),
    ],
)
def test_speakable(said: str, spoken: str) -> None:
    assert speakable(said) == spoken


@pytest.mark.parametrize(
    ("said", "spoken"),
    [
        ("মোট ৳360।", "মোট তিনশো ষাট টাকা।"),
        ("মোট ৳১,০৪৫.৫০", "মোট এক হাজার পঁয়তাল্লিশ টাকা পঞ্চাশ পয়সা"),  # Bangla digits too
        ("৳150000", "এক লাখ পঞ্চাশ হাজার টাকা"),
        ("7:30 pm এ", "সন্ধ্যা সাতটা বেজে ত্রিশ মিনিট এ"),
        ("সন্ধ্যা 7:30 pm এ", "সন্ধ্যা সাতটা বেজে ত্রিশ মিনিট এ"),  # not সন্ধ্যা twice
        ("10:00 থেকে 19:00", "দশটা থেকে সন্ধ্যা সাতটা"),  # a bare 10:00 gets no part of day
        ("12:15 am", "রাত বারোটা বেজে পনের মিনিট"),
        ("অর্ডার O-104।", "অর্ডার ও একশো চার।"),
        ("ফোন 01712-345678", "ফোন শূন্য এক সাত এক দুই তিন চার পাঁচ ছয় সাত আট"),
        ("৭টায়", "সাতটায়"),
        ("2 × কটন টুপি", "দুই কটন টুপি"),
    ],
)
def test_speakable_in_bangla(said: str, spoken: str) -> None:
    assert speakable(said, "bn") == spoken
