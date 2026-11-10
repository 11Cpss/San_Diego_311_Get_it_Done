"""Structured logging: every step writes exactly one JSON line.

Why JSON? CloudWatch Logs Insights can query fields directly, e.g.
    fields @timestamp, step, source, rows | filter level = "ERROR"
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from typing import Any


def log_step(step: str, level: str = "INFO", **fields: Any) -> dict:
    """Print one JSON log line and return the record (handy in tests)."""
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "level": level,
        "step": step,
        **fields,
    }
    # default=str makes dates/Decimals/etc. serialisable instead of crashing.
    print(json.dumps(record, default=str), file=sys.stdout, flush=True)
    return record


class timed:
    """Context manager that logs a step with its duration in milliseconds.

    with timed("download", source="open") as extra:
        ...
        extra["bytes"] = 123   # extra fields get added to the log line
    """

    def __init__(self, step: str, **fields: Any):
        self.step = step
        self.fields = fields
        self.extra: dict[str, Any] = {}

    def __enter__(self) -> dict[str, Any]:
        self._t0 = time.perf_counter()
        return self.extra

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = round((time.perf_counter() - self._t0) * 1000)
        if exc is None:
            log_step(self.step, duration_ms=ms, **self.fields, **self.extra)
        else:
            log_step(self.step, level="ERROR", duration_ms=ms, error=repr(exc),
                     **self.fields, **self.extra)
        return False  # never swallow exceptions
