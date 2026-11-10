from typing import Any

from heydesk.runtime.trace import summary


def a_trace(**changes: Any) -> dict[str, Any]:
    trace = {
        "channel": "web",
        "first_output_ms": 612,
        "error": None,
        "total_ms": 1843,
        "steps": [
            {"name": "model", "tokens": {"input": 2000, "cached": 0, "output": 100}},
            {"name": "tool", "tool": "find_product"},
            {"name": "model", "tokens": {"input": 150, "cached": 0, "output": 31}},
        ],
    }
    return trace | changes


def test_a_turn_sums_up_in_one_line() -> None:
    assert summary(a_trace()) == (
        "model → find_product → model · 1843 ms, first words at 612 ms · 2281 tokens · web"
    )


def test_a_failed_turn_says_why() -> None:
    failed = a_trace(first_output_ms=None, error="no usable reply (stop: length)")
    assert summary(failed) == (
        "model → find_product → model · 1843 ms · 2281 tokens · no usable reply (stop: length) · web"
    )
