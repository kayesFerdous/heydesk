"""Lists as files a spreadsheet opens: the shop's orders export."""

import csv
import io
from typing import Any

from fastapi import Response

# Limitation: an export is the newest 10,000 rows in one file; page it if a business has more
EXPORT_ROWS = 10_000
FORMULA = ("=", "+", "-", "@", "\t", "\r")  # a spreadsheet runs a cell that starts with these


def phone_cell(phone: str) -> str:
    """01812345678 as "01812-345678": a spreadsheet reads the digits alone as a number and
    drops the 0."""
    return f"{phone[:5]}-{phone[5:]}" if len(phone) == 11 and phone.isdigit() else phone


def amount_cell(minor: int) -> int | float:
    """৳1,045.50 as 1045.5, and ৳3,200 as 3200: a number a spreadsheet adds up."""
    return minor // 100 if minor % 100 == 0 else minor / 100


def csv_file(name: str, columns: list[str], rows: list[list[Any]]) -> Response:
    """A list as a file a spreadsheet opens: UTF-8 with a BOM, so Excel reads Bangla. A
    visitor's words that start like a formula get a ' first, so they stay words."""

    def cell(value: Any) -> Any:
        return f"'{value}" if isinstance(value, str) and value.startswith(FORMULA) else value

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows([cell(v) for v in row] for row in rows)
    return Response(
        "\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )
