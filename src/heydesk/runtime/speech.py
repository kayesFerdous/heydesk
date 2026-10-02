"""Display and spoken forms of values (D7, D20).

The model always sees display forms ("৳450", "7:30 pm"); on voice, `speakable()`
turns each sentence into words just before it's spoken.
"""

import re
from datetime import time

from num2words import num2words  # type: ignore[import-untyped]

SYMBOLS = {"BDT": "৳", "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def money(minor: int, currency: str) -> str:
    """45000 BDT → "৳450"; 104550 → "৳1,045.50". Amounts are always in minor units."""
    major, cents = divmod(minor, 100)
    symbol = SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{major:,}" + (f".{cents:02d}" if cents else "")


def clock_time(t: time) -> str:
    """19:30 → "7:30 pm"; 12:05 → "12:05 pm"."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


CURRENCY_WORDS = {
    "BDT": ("taka", "poisha"),
    "USD": ("dollars", "cents"),
    "EUR": ("euros", "cents"),
    "GBP": ("pounds", "pence"),
    "INR": ("rupees", "paise"),
}
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

PHONE = re.compile(r"\+?\d(?:[ -]?\d){6,}")  # 7 or more digits: said one by one
MONEY = re.compile(r"([৳$€£₹])\s?(\d[\d,]*)(?:\.(\d{2}))?")
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\b\.?)?", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d,]*")


def speakable(text: str) -> str:
    """ "2 × Borhani, total ৳1,030; pickup 4:05 pm; phone 01712345678" in words, for the TTS."""
    text = PHONE.sub(_phone, text)
    text = MONEY.sub(_money, text)
    text = CLOCK.sub(_clock, text)
    text = text.replace(" × ", " ").replace("#", "number ")
    return NUMBER.sub(lambda m: _words(m[0]), text)


def _words(digits: str) -> str:
    return str(num2words(int(digits.replace(",", ""))))


def _phone(m: re.Match[str]) -> str:
    said = " ".join(DIGITS[int(c)] for c in m[0] if c.isdigit())
    return ("plus " if m[0].startswith("+") else "") + said


def _money(m: re.Match[str]) -> str:
    code = next(code for code, symbol in SYMBOLS.items() if symbol == m[1])
    major, minor = CURRENCY_WORDS[code]
    said = f"{_words(m[2])} {major}"
    if m[3] and int(m[3]):
        said += f" {_words(m[3])} {minor}"
    return said


def _clock(m: re.Match[str]) -> str:
    minute = int(m[2])
    said = _words(m[1])
    if minute:
        said += (" oh " if minute < 10 else " ") + _words(m[2])
    elif not m[3]:
        said += " o'clock"
    return said + (f" {m[3].lower()}.m." if m[3] else "")
