import logging

from heydesk.logs import ReadableFormatter


def test_a_readable_line_shows_the_details() -> None:
    record = logging.makeLogRecord(
        {
            "name": "heydesk.services.calls",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": "call done",
            "call_id": "c1",
            "outcome": "confirmed",
            "trace": {"steps": []},  # nested: only in the JSON lines
        }
    )
    line = ReadableFormatter(colour=False).format(record)
    assert line.endswith("INFO     calls          call done  call_id=c1 outcome=confirmed")
