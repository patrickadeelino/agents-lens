"""Read raw and timed Codex events from a saved JSONL trace."""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .trace_events import TimedEvent, TraceEvent


@dataclass(frozen=True)
class UntimedEvent:
    event: TraceEvent
    elapsed_ns: None = None


type TraceRecord = TimedEvent | UntimedEvent


def read_trace(lines: Iterable[str]) -> Iterator[TraceRecord]:
    """Yield one record per line in trace order, preserving available offsets."""
    for line in lines:
        payload = json.loads(line)
        if isinstance(payload, dict) and "event" in payload and "elapsed_ns" in payload:
            yield TimedEvent.model_validate(payload)
        else:
            yield UntimedEvent(event=TraceEvent.model_validate(payload))


def read_timed_trace(lines: Iterable[str]) -> Iterator[TimedEvent]:
    """Read a trace for a consumer that requires an offset on every record."""
    for record in read_trace(lines):
        if record.elapsed_ns is None:
            raise ValueError("Timed replay requires an arrival offset for every event.")
        yield record
