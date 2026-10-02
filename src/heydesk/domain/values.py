"""Values that carry a guarantee in their type (rule 2)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Confirmed[T]:
    """`value`, read back to the visitor, who said yes.

    Only `runtime.guards.confirm` makes one (`scripts/check_imports.sh` checks),
    so a service that takes `Confirmed[...]` can't be called without the commit check.
    """

    value: T


@dataclass(frozen=True)
class NewOrder:
    """An order ready to save. Money in minor units; `pickup_time` in UTC."""

    lines: list[dict[str, Any]]  # {code, name, quantity, price}, as update_order saved them
    total: int
    currency: str
    pickup_time: datetime
    customer_name: str
    customer_phone: str
    note: str = ""
