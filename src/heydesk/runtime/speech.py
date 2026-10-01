"""Display and spoken forms of values (D7). The channel picks one in code.

Display forms only for now; spoken forms arrive with voice (day 12).
"""

SYMBOLS = {"BDT": "৳", "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def money(minor: int, currency: str) -> str:
    """45000 BDT → "৳450"; 104550 → "৳1,045.50". Amounts are always in minor units."""
    major, cents = divmod(minor, 100)
    symbol = SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{major:,}" + (f".{cents:02d}" if cents else "")
