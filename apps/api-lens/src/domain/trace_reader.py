"""Read timed Codex events from a saved JSONL trace."""

from collections.abc import Iterable, Iterator

from .trace_events import TimedEvent


def read_timed_trace(lines: Iterable[str]) -> Iterator[TimedEvent]:
    for line in lines:
        yield TimedEvent.model_validate_json(line)
