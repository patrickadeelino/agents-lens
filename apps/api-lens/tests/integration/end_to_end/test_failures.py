"""Keep what was observed when an execution breaks."""

import json
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi import FastAPI, Response

from src.backend.infrastructure.executor_client import ExecutorClient
from src.executor.agents.codex.catalog_run import CatalogRun
from src.executor.process import agent_process
from src.shared.trace_events import TimedEvent
from tests.integration.end_to_end.support import (
    capture_directory,
    next_frame,
    prompt_body,
    running_server,
)


@pytest.mark.usefixtures("executor_socket")
def test_run_reports_subprocess_startup_failure(
    live_http: tuple[httpx.Client, Event], run_config: CatalogRun,
) -> None:
    backend, _ = live_http
    run_config.binary.unlink()
    with backend.stream("POST", "/runs", json=prompt_body("Read the example.")) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_records_when_app_server_exits_before_turn_completion(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json=prompt_body("exit 7")) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, record = next_frame(frames)
        assert kind == "record"
        assert next_frame(frames)[1]["event"] == {"type": "turn.started"}
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    trace = (directory / "events.timed.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(trace[0]) == record
    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] is None


@pytest.mark.parametrize(
    "tail",
    [(), ({"exit_code": 0}, {"stdout": '{"type": "turn.completed"}'})],
    ids=["missing-exit-status", "stdout-after-exit-status"],
)
def test_run_keeps_observed_records_and_rejects_invalid_executor_streams(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    tail: tuple[dict[str, object], ...],
) -> None:
    monkeypatch.setattr(ExecutorClient, "selected_folder", lambda *_: str(run_workspace / "example"))
    monkeypatch.setattr(ExecutorClient, "check_agent", lambda *_: None)
    invalid_executor = FastAPI()
    event = {"type": "turn.started"}

    @invalid_executor.post("/run")
    def invalid_run() -> Response:
        messages = ({"stdout": json.dumps(event)}, *tail)
        body = "".join(json.dumps(message) + "\n" for message in messages)
        return Response(body, media_type="application/x-ndjson")

    unix_path = run_workspace / "executor.sock"
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(unix_path))
    backend, _ = live_http
    with (
        running_server(invalid_executor, Event(), unix_path=unix_path),
        backend.stream("POST", "/runs", json=prompt_body("Read the example.")) as response,
    ):
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, record = next_frame(frames)
        assert (kind, record["event"]) == ("record", event)
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    assert json.loads((directory / "events.timed.jsonl").read_text(encoding="utf-8")) == record
    assert json.loads((directory / "result.json").read_text())["exit_code"] is None


def test_run_reports_unconfirmed_result_when_socket_is_unreachable(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ExecutorClient, "selected_folder", lambda *_: str(run_workspace / "example"))
    monkeypatch.setattr(ExecutorClient, "check_agent", lambda *_: None)
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "missing.sock"))
    backend, _ = live_http

    with backend.stream("POST", "/runs", json=prompt_body("Read the example.")) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    assert (directory / "events.timed.jsonl").read_text() == ""
    assert json.loads((directory / "result.json").read_text())["exit_code"] is None
    detail = backend.get(f"/threads/by-run/{directory.name}")
    assert detail.status_code == 200
    assert detail.json()["thread_id"] is None
    turn = detail.json()["turns"][0]
    assert turn["status"] == "failed"
    assert turn["summary"]["total_duration_ns"] > 0
    assert turn["summary"]["observed_duration_ns"] is None


    stderr = capsys.readouterr().err
    logs = {record["event"]: record for record in map(json.loads, stderr.splitlines())}
    assert logs["run.accepted"]["run_id"] == directory.name
    assert logs["run.capture.failed"]["error_code"] == "executor_unreachable"
    assert logs["run.capture.failed"]["run_id"] == directory.name
    assert logs["run.capture.failed"]["event_count"] == 0
    assert "executor.request.first_event_received" not in logs
    assert "run.sse.first_record_sent" not in logs
    assert "run.sse.record_sent" not in logs
    assert logs["run.sse.end_sent"]["event_count"] == 0
    assert "missing.sock" not in stderr
    assert "Read the example." not in stderr


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_trace_when_a_line_exceeds_the_limit(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_process, "MAX_EVENT_BYTES", 32 * 1024)
    backend, _ = live_http

    with backend.stream("POST", "/runs", json=prompt_body("emit a big record")) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        _, thread = next_frame(frames)
        kind, record = next_frame(frames)
        assert (kind, TimedEvent.model_validate(record).event.type) == ("record", "turn.started")
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "event_too_large"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    trace = (directory / "events.timed.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in trace.splitlines()] == [thread, record]
    assert json.loads((directory / "result.json").read_text())["exit_code"] is None
