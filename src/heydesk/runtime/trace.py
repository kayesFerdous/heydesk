"""The per-turn trace (rule 7): steps, timings, tokens, errors. Logged when the turn finishes."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from heydesk.ports.llm import Usage


class Trace:
    def __init__(self, channel: str) -> None:
        self.started = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {
            "channel": channel,
            "first_output_ms": None,
            "cost_micros": 0,
            "error": None,
        }

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)

    @contextmanager
    def step(self, name: str, **detail: Any) -> Iterator[dict[str, Any]]:
        """Times one model call or tool run. Callers add fields to the yielded dict."""
        step: dict[str, Any] = {"name": name, **detail, "at_ms": self.elapsed_ms()}
        self.steps.append(step)
        started = time.perf_counter()
        try:
            yield step
        finally:
            step["ms"] = round((time.perf_counter() - started) * 1000)

    def first_output(self, step: dict[str, Any]) -> None:
        """The first token of a step (its `ttft_ms`) and of the turn (`first_output_ms`)."""
        step.setdefault("ttft_ms", self.elapsed_ms() - step["at_ms"])
        if self.data["first_output_ms"] is None:
            self.data["first_output_ms"] = self.elapsed_ms()

    def add_usage(self, step: dict[str, Any], usage: Usage) -> None:
        step["tokens"] = {"input": usage.input, "cached": usage.cached, "output": usage.output}
        self.data["cost_micros"] += usage.cost_micros

    def to_dict(self) -> dict[str, Any]:
        return {**self.data, "total_ms": self.elapsed_ms(), "steps": self.steps}
