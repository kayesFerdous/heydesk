"""P50/P95 of the voice first-sound time, from a server log.

uv run uvicorn heydesk.main:app 2>&1 | tee voice.log   # then make some calls
uv run python scripts/latency.py voice.log
"""

import re
import statistics
import sys
from pathlib import Path

FIRST_SOUND = re.compile(r"first sound: ([\d.]+)s")
HEARD = re.compile(r"heard: ([\d.]+)s")  # a call on Chorus: each utterance
MODEL = re.compile(r'"first_output_ms": (\d+)')  # the turn's trace: its first token


def report(name: str, seconds: list[float]) -> str:
    if len(seconds) < 2:
        return f"{name}: {len(seconds)} sample(s), need at least 2"
    cuts = statistics.quantiles(seconds, n=20, method="inclusive")  # every 5%
    return f"{name}: P50 {statistics.median(seconds):.2f}s, P95 {cuts[18]:.2f}s ({len(seconds)} replies)"


def main(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    print(report("last word → first sound", [float(s) for s in FIRST_SOUND.findall(text)]))
    print(report("hearing an utterance (Chorus)", [float(s) for s in HEARD.findall(text)]))
    print(report("model's first token", [int(ms) / 1000 for ms in MODEL.findall(text)]))


if __name__ == "__main__":
    main(sys.argv[1])
