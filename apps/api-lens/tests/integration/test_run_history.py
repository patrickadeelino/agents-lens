import json
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app import app
from src.api import dependencies
from src.domain.trace_events import TimedEvent, TraceEvent
from src.infrastructure.capture_files import CaptureFiles


@pytest.fixture
def history_capture(monkeypatch: pytest.MonkeyPatch, capture_root: Path) -> Iterator[CaptureFiles]:
    with TemporaryDirectory(prefix="test-history-", dir=capture_root) as directory:
        root = Path(directory)
        monkeypatch.setattr(dependencies, "CAPTURE_ROOT", root)
        yield CaptureFiles.create(root, prefix="codex-prompt-")


def record(offset_ms: int, event_type: str, **payload: object) -> str:
    event = TraceEvent.model_validate({"type": event_type, **payload})
    return TimedEvent(elapsed_ns=offset_ms * 1_000_000, event=event).model_dump_json() + "\n"


def test_history_paginates_in_order_and_calculates_command_duration_across_pages(
    history_capture: CaptureFiles,
) -> None:
    command = {"id": "item_1", "type": "command_execution"}
    with history_capture.open_trace() as trace:
        trace.writelines([
            record(100, "item.started", item={**command, "command": "cat README.md", "status": "in_progress"}),
            record(200, "future.event", feature="unchanged"),
            record(350, "item.completed", item={**command, "status": "completed", "exit_code": 0}),
            record(400, "turn.completed"),
        ])
    history_capture.write_result(0)
    path = f"/runs/{history_capture.directory.name}/events"

    with TestClient(app) as client:
        newest = client.get(path, params={"limit": 2})
        assert newest.status_code == 200
        assert newest.headers["cache-control"] == "no-store"
        latest = newest.json()
        older = client.get(path, params={"limit": 2, "before": latest["next_before"]})

    assert latest["run_id"] == history_capture.directory.name
    assert latest["status"] == "completed"
    assert latest["exit_code"] == 0
    assert latest["last_turn"] == "turn.completed"
    assert [entry["event_id"] for entry in latest["events"]] == [3, 4]
    assert latest["latest_event_id"] == 4
    assert latest["next_before"] == 3
    assert latest["commands"]["item_1"] == {
        "command": "cat README.md", "status": "completed", "exit_code": 0, "observed_duration_ns": 250_000_000,
    }
    assert older.status_code == 200
    previous = older.json()
    assert [entry["event_id"] for entry in previous["events"]] == [1, 2]
    assert previous["events"][1]["record"]["event"] == {"type": "future.event", "feature": "unchanged"}
    assert previous["latest_event_id"] == 4
    assert previous["next_before"] is None
    assert previous["commands"] == latest["commands"]


def test_history_keeps_a_command_failure_without_an_observed_start(history_capture: CaptureFiles) -> None:
    with history_capture.open_trace() as trace:
        trace.writelines([
            record(350, "item.completed", item={
                "id": "item_1", "type": "command_execution", "command": "cat missing.txt",
                "status": "failed", "exit_code": 1,
            }),
            record(500, "turn.completed"),
        ])
    history_capture.write_result(0)

    with TestClient(app) as client:
        response = client.get(f"/runs/{history_capture.directory.name}/events")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    command = response.json()["commands"]["item_1"]
    assert command["status"] == "failed"
    assert command["exit_code"] == 1
    assert command["observed_duration_ns"] is None


def test_history_keeps_only_complete_lines_before_a_result_exists(history_capture: CaptureFiles) -> None:
    trace = history_capture.directory / "events.timed.jsonl"
    trace.write_bytes(record(0, "turn.started").encode() + b'{"elapsed_ns":1,"event":{"type":"\xc3')
    path = f"/runs/{history_capture.directory.name}/events"

    with TestClient(app) as client:
        response = client.get(path)
        history_capture.write_result(0)
        invalid = client.get(path)

    assert response.status_code == 200
    assert response.json()["latest_event_id"] == 1
    assert response.json()["status"] == "unconfirmed"
    assert invalid.status_code == 503


def test_history_returns_safe_errors_for_missing_invalid_and_linked_captures(history_capture: CaptureFiles) -> None:
    path = f"/runs/{history_capture.directory.name}/events"
    with TestClient(app) as client:
        missing_run = client.get("/runs/missing-run/events")
        assert missing_run.status_code == 404
        assert missing_run.json() == {"detail": "Run was not found."}
        assert client.get("/runs/%2E%2E/events").status_code == 422
        missing_trace = client.get(path)
        assert missing_trace.status_code == 404
        assert missing_trace.json() == {"detail": "Run trace was not found."}
        assert client.get(path, params={"limit": 101}).status_code == 422

        with history_capture.open_trace() as trace:
            trace.write(json.dumps({"invalid": "private capture data"}) + "\n")
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "Run capture is unavailable."}

        alias = history_capture.directory.parent / "alias"
        alias.symlink_to(history_capture.directory, target_is_directory=True)
        assert client.get("/runs/alias/events").status_code == 404


def test_stream_uses_last_event_id_on_reconnect(history_capture: CaptureFiles) -> None:
    with history_capture.open_trace() as trace:
        trace.writelines([record(0, "turn.started"), record(1, "turn.completed")])
    history_capture.write_result(0)
    path = f"/runs/{history_capture.directory.name}/stream"

    with TestClient(app) as client:
        response = client.get(path, params={"after": 0}, headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    assert response.text.splitlines() == [
        "id: 2", "event: record", 'data: {"elapsed_ns":1000000,"event":{"type":"turn.completed"}}', "",
        "event: end", 'data: {"exit_code": 0}', "",
    ]


def test_history_retries_when_the_worker_has_not_opened_the_trace(history_capture: CaptureFiles) -> None:
    path = f"/runs/{history_capture.directory.name}/events"
    with TestClient(app) as client, history_capture.track_activity():
        starting = client.get(path)
        assert starting.status_code == 409
        assert starting.json() == {"detail": "Run capture is starting."}

        with history_capture.open_trace() as trace:
            trace.write(record(0, "turn.started"))
        response = client.get(path)

    assert response.status_code == 200
    assert response.json()["last_turn"] == "turn.started"


def test_stream_ends_when_there_is_no_active_capture_or_final_result(history_capture: CaptureFiles) -> None:
    with history_capture.open_trace() as trace:
        trace.write(record(0, "turn.started"))
    path = f"/runs/{history_capture.directory.name}/stream"

    with TestClient(app) as client:
        response = client.get(path, params={"after": 1})

    assert response.status_code == 200
    assert response.text == 'event: end\ndata: {"exit_code": null, "error": "result_unconfirmed"}\n\n'
