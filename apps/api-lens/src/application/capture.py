"""Add monotonic arrival offsets to Codex CLI JSONL."""

import json
from collections.abc import Callable, Iterable, Iterator
from time import monotonic_ns
from typing import TextIO

from src.domain.trace_events import TimedEvent, TraceEvent

type RecordCallback = Callable[[TimedEvent], None]


class EventCapture:
    def __init__(self, *, clock_ns: Callable[[], int] = monotonic_ns) -> None:
        self._clock_ns = clock_ns

    def read(self, lines: Iterable[str]) -> Iterator[TimedEvent]:
        """Yield each event with nanoseconds elapsed since reading started."""
        started_at_ns = self._clock_ns()
        for line in lines:
            received_at_ns = self._clock_ns()
            yield TimedEvent(
                elapsed_ns=received_at_ns - started_at_ns,
                event=TraceEvent.model_validate_json(line),
            )

    def write(
        self,
        lines: Iterable[str],
        output: TextIO,
        *,
        on_record: RecordCallback | None = None,
    ) -> None:
        """Write each timed record and notify the callback after flushing it."""
        for record in self.read(lines):
            line = json.dumps(record.model_dump(), ensure_ascii=False)
            output.write(line + "\n")
            output.flush()
            if on_record is not None:
                on_record(record)
