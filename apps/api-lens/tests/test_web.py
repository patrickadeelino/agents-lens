import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app, get_replay_events
from src.capture import EventCapture
from src.trace_events import TimedEvent


def test_events_streams_injected_records_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {"elapsed_ns": 0, "event": {"type": "turn.started"}},
        {
            "elapsed_ns": 0,
            "event": {"type": "item.completed", "item": {"type": "agent_message", "text": "one\ntwo"}},
        },
        {"elapsed_ns": 0, "event": {"type": "future.event", "detail": {"label": "próximo"}}},
        {"elapsed_ns": 0, "event": {"type": "turn.completed", "usage": {"output_tokens": 7}}},
    ]

    def load_test_trace() -> tuple[TimedEvent, ...]:
        lines = (json.dumps(record["event"]) for record in records)
        return tuple(EventCapture(clock_ns=lambda: 0).read(lines))

    monkeypatch.setitem(app.dependency_overrides, get_replay_events, load_test_trace)
    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    frames = response.text.split("\n\n")
    assert frames[-2:] == ["event: end\ndata: {}", ""]
    decoded_records = []
    for frame in frames[:-2]:
        name, data = frame.splitlines()
        assert name == "event: record"
        assert data.startswith("data: ")
        decoded_records.append(json.loads(data.removeprefix("data: ")))
    assert decoded_records == records


def test_events_returns_503_for_a_trace_without_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    raw_trace = root / "examples" / "traces" / "readme-run.sanitized.jsonl"
    monkeypatch.setattr("app.REPLAY_TRACE", raw_trace)

    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 503
    assert response.json() == {"detail": "Replay trace is unavailable."}
