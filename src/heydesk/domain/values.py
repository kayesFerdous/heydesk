"""Values that carry a guarantee in their type (rule 2), and the codes visitors are told."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Confirmed[T]:
    """`value`, read back to the visitor, who said yes.

    Only `runtime.guards.confirm` makes one (`scripts/check_imports.sh` checks),
    so a store method that takes `Confirmed[...]` can't be called without the commit check.
    """

    value: T


def record_code(letter: str, number: int) -> str:
    """What the visitor and the business see: a letter and the number, "A-104"."""
    return f"{letter}-{number}"


CODE = re.compile(r"\s*([a-z])?\s*-?\s*(\d{1,9})\s*", re.IGNORECASE)


def parse_code(text: str) -> tuple[str, int] | None:
    """ "A-104", "a104", "a 104" or "104" → ("A", 104); the letter is "" when left out."""
    found = CODE.fullmatch(text)
    return ((found[1] or "").upper(), int(found[2])) if found else None
