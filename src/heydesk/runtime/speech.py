"""Display and spoken forms of values (D7). The channel picks one in code.

Display forms only for now; spoken forms arrive with voice (day 12).
"""

from datetime import time

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
