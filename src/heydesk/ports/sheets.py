"""Reading a business's linked Google Sheet as CSV: one function, `FetchCsv`."""

from collections.abc import Awaitable, Callable


class SheetUnreadable(Exception):
    """The sheet couldn't be read: not shared by link, gone, or Google didn't answer. The
    message is for the owner."""


type FetchCsv = Callable[[str], Awaitable[str]]  # the sheet's CSV export URL → its text
