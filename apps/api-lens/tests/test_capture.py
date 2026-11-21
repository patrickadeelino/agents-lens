import json

from src.capture import EventCapture
from src.trace_events import TimedEvent


def test_each_line_is_timestamped_before_reading_the_next() -> None:
    now_ns = 4_000_000_000
    started = {"type": "turn.started"}
    completed = {"type": "turn.completed", "usage": {"output_tokens": 7}}

    def arriving_lines():
        nonlocal now_ns
        now_ns += 25_000_000
        yield json.dumps(started) + "\n"
        now_ns += 1_000_000_000
        yield json.dumps(completed) + "\n"

    records = EventCapture(clock_ns=lambda: now_ns).read(arriving_lines())

    start_record = next(records)
    assert isinstance(start_record, TimedEvent)
    assert start_record.elapsed_ns == 25_000_000
    assert start_record.event.type == "turn.started"
    assert now_ns == 4_025_000_000
    completion_record, = records
    assert completion_record.model_dump() == {"elapsed_ns": 1_025_000_000, "event": completed}
