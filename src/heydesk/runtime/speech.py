"""Display and spoken forms of values (D7, D20).

The model always sees display forms ("৳450", "7:30 pm"); on voice, `speakable()`
turns each sentence into words just before it's spoken, in English or in Bangla (D84).
"""

import re
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from num2words import num2words  # type: ignore[import-untyped]

SYMBOLS = {"BDT": "৳", "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def money(minor: int, currency: str) -> str:
    """45000 BDT → "৳450"; 104550 → "৳1,045.50". Amounts are always in minor units."""
    major, cents = divmod(minor, 100)
    symbol = SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{major:,}" + (f".{cents:02d}" if cents else "")


def local_time(at: datetime, timezone: str) -> str:
    """A stored time (UTC) in the business's own, for a file: "2026-09-29 14:00"."""
    return (
        (at if at.tzinfo else at.replace(tzinfo=UTC))
        .astimezone(ZoneInfo(timezone))
        .strftime("%Y-%m-%d %H:%M")
    )


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


def speakable(text: str, language: str = "en") -> str:
    """ "2 × Borhani, total ৳1,030; pickup 4:05 pm; phone 01712345678" in words, for the TTS."""
    if language == "bn":
        return _bangla(text)
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


# Bangla (D84). The Bangla voice reads Bangla letters only, so every number becomes Bangla words.
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BN_DIGIT_WORDS = ["শূন্য", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়"]
BN_CURRENCY_WORDS = {
    "BDT": ("টাকা", "পয়সা"),
    "USD": ("ডলার", "সেন্ট"),
    "EUR": ("ইউরো", "সেন্ট"),
    "GBP": ("পাউন্ড", "পেন্স"),
    "INR": ("রুপি", "পয়সা"),
}
CODE = re.compile(r"\b([AMOT])-(\d+)")  # O-104, A-104, T-101, M-104: a record's code (D38)
BN_LETTERS = {"A": "এ", "M": "এম", "O": "ও", "T": "টি"}
TWICE = re.compile(r"(সকাল|দুপুর|বিকেল|সন্ধ্যা|রাত) \1")


def _bangla(text: str) -> str:
    """ "মোট ৳360, 7:30 pm, O-104" → "মোট তিনশো ষাট টাকা, সন্ধ্যা সাতটা বেজে ত্রিশ মিনিট, ও একশো চার"."""
    text = text.translate(BN_DIGITS)  # the model may write ৩৬০ as well as 360
    text = PHONE.sub(_bn_phone, text)
    text = CODE.sub(lambda m: f"{BN_LETTERS[m[1]]} {_bn(m[2])}", text)
    text = MONEY.sub(_bn_money, text)
    text = CLOCK.sub(_bn_clock, text)
    text = TWICE.sub(r"\1", text)  # "সন্ধ্যা 7:30 pm" was "সন্ধ্যা সন্ধ্যা সাতটা…"
    text = text.replace(" × ", " ").replace("#", "নম্বর ")
    return NUMBER.sub(lambda m: _bn(m[0]), text)


def _bn(digits: str) -> str:
    """num2words writes the formal শত ("তিনশত"); people say শো ("তিনশো")."""
    return str(num2words(int(digits.replace(",", "")), lang="bn")).replace("শত", "শো")


def _bn_phone(m: re.Match[str]) -> str:
    said = " ".join(BN_DIGIT_WORDS[int(c)] for c in m[0] if c.isdigit())
    return ("প্লাস " if m[0].startswith("+") else "") + said


def _bn_money(m: re.Match[str]) -> str:
    code = next(code for code, symbol in SYMBOLS.items() if symbol == m[1])
    major, minor = BN_CURRENCY_WORDS[code]
    said = f"{_bn(m[2])} {major}"
    if m[3] and int(m[3]):
        said += f" {_bn(m[3])} {minor}"
    return said


def _bn_clock(m: re.Match[str]) -> str:
    """ "7:30 pm" → "সন্ধ্যা সাতটা বেজে ত্রিশ মিনিট"; "10:00" → "দশটা"."""
    hour, minute = int(m[1]), int(m[2])
    if m[3]:
        hour = hour % 12 + (12 if m[3].lower() == "p" else 0)
    said = f"{_bn(str(hour % 12 or 12))}টা"
    if minute:
        said += f" বেজে {_bn(m[2])} মিনিট"
    if m[3] or hour > 12:  # a bare "7:30" might be morning or evening: no part of the day
        said = f"{_bn_part_of_day(hour)} {said}"
    return said


def _bn_part_of_day(hour: int) -> str:
    if hour < 4:
        return "রাত"
    if hour < 12:
        return "সকাল"
    if hour < 15:
        return "দুপুর"
    if hour < 18:
        return "বিকেল"
    if hour < 20:
        return "সন্ধ্যা"
    return "রাত"
