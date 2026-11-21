"""Encode trace records as Server-Sent Events."""

import json

from src.domain.trace_events import TimedEvent


def record_frame(record: TimedEvent) -> str:
    return f"event: record\ndata: {record.model_dump_json()}\n\n"


def end_frame(payload: dict[str, int | str | None] | None = None) -> str:
    return f"event: end\ndata: {json.dumps(payload or {})}\n\n"
