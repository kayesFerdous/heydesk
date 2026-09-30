from datetime import UTC, datetime, timedelta, timezone

import pytest

from heydesk.db import UTCDateTime

DHAKA = timezone(timedelta(hours=6))


def test_utc_datetime_stores_utc_and_reads_back_aware() -> None:
    t = UTCDateTime()
    stored = t.process_bind_param(datetime(2026, 9, 29, 14, 0, tzinfo=DHAKA), None)  # type: ignore[arg-type]
    assert stored == datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
    read = t.process_result_value(stored.replace(tzinfo=None), None)  # type: ignore[union-attr, arg-type]
    assert read == datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
    assert read is not None and read.tzinfo is UTC


def test_utc_datetime_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 9, 29, 8, 0), None)  # type: ignore[arg-type]  # noqa: DTZ001
