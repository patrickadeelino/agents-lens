import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app import app
from src.api import dependencies
from src.application import replay
from src.application.capture import EventCapture
from src.application.replay import TimedReplay


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

    def load_test_trace() -> TimedReplay:
        lines = (json.dumps(record["event"]) for record in records)
        return TimedReplay(tuple(EventCapture(clock_ns=lambda: 0).read(lines)))

    monkeypatch.setitem(app.dependency_overrides, dependencies.get_replay, load_test_trace)
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


def test_events_returns_503_for_a_trace_without_offsets(
    monkeypatch: pytest.MonkeyPatch, project_root: Path,
) -> None:
    raw_trace = project_root / "examples" / "traces" / "readme-run.sanitized.jsonl"
    monkeypatch.setattr(dependencies, "REPLAY_TRACE", raw_trace)

    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 503
    assert response.json() == {"detail": "Replay trace is unavailable."}


@pytest.mark.parametrize("offsets", [(), (10, 0)], ids=["empty", "out-of-order"])
def test_events_returns_503_for_invalid_replay_offsets(
    monkeypatch: pytest.MonkeyPatch, capture_root: Path, offsets: tuple[int, ...],
) -> None:
    with TemporaryDirectory(prefix="test-replay-", dir=capture_root) as directory:
        trace = Path(directory) / "invalid.timed.jsonl"
        trace.write_text(
            "".join(
                json.dumps({"elapsed_ns": offset, "event": {"type": "turn.started"}}) + "\n"
                for offset in offsets
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(dependencies, "REPLAY_TRACE", trace)

        with TestClient(app) as client:
            response = client.get("/events")

    assert response.status_code == 503
    assert response.json() == {"detail": "Replay trace has invalid arrival offsets."}


def test_events_replays_real_sanitized_delegation(
    monkeypatch: pytest.MonkeyPatch, project_root: Path,
) -> None:
    async def skip_delay(_: float) -> None:
        pass

    fixture = project_root / "examples" / "traces" / "catalog-delegation.timed.sanitized.jsonl"
    monkeypatch.setattr(dependencies, "REPLAY_TRACE", fixture)
    monkeypatch.setattr(replay, "sleep", skip_delay)
    monkeypatch.delenv("AGENT_LENS_EXECUTOR_SOCKET", raising=False)
    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 200
    payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert payloads.pop() == {}
    assert payloads == [json.loads(line) for line in fixture.read_text().splitlines()]
    events = [record["event"] for record in payloads]
    edge = next(event for event in events if event["type"] == "delegation.edge")
    command = next(event for event in events if event.get("observation", {}).get("item", {}).get("aggregated_output"))
    assert command["thread_id"] == edge["child_thread_id"]
    assert "catalog.py" in command["observation"]["item"]["command"]
