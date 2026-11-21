import json
import socket
import sys
from asyncio import Queue
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Response
from starlette.types import ASGIApp, Receive, Scope, Send

import executor
from app import app
from executor import app as executor_app
from src.api import dependencies
from src.application import capture_run
from src.domain.trace_events import TimedEvent
from src.infrastructure.read_only_run import ReadOnlyRun

LITERAL_PROMPT = '  --help\nLeia o README "juniper" ☕.\n'
PROBE = """
import json
import os
import sys
import time
from pathlib import Path

assert sys.argv[-2:] == ["--", "-"]
assert 'default_permissions="agent-lens-readme"' in sys.argv
assert 'permissions.agent-lens-readme.network.enabled=false' in sys.argv
assert 'shell_environment_policy.inherit="none"' in sys.argv
prompt = sys.stdin.buffer.read().decode("utf-8")
print(json.dumps({"type": "turn.started", "prompt": prompt, "cwd": str(Path.cwd())}), flush=True)
if prompt == "report a diagnostic":
    print(json.dumps({"type": "error", "message": os.environ["CODEX_API_KEY"]}), flush=True)
    raise SystemExit(7)
if prompt == "emit a big record":
    print(json.dumps({"type": "item.completed", "item": {
        "id": "item_1", "type": "agent_message", "text": "x" * (128 * 1024),
    }}), flush=True)
    print('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "exit 7":
    raise SystemExit(7)
release = Path(os.environ["CODEX_HOME"]) / "release"
deadline = time.monotonic() + 5
while not release.exists():
    if time.monotonic() >= deadline:
        raise SystemExit(8)
    time.sleep(0.01)
print('{"type":"turn.completed"}', flush=True)
"""


@contextmanager
def running_server(
    application: ASGIApp, request_finished: Event, *, unix_path: Path | None = None,
) -> Iterator[str]:
    async def observe(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await application(scope, receive, send)
        finally:
            request_finished.set()

    listener = socket.socket(socket.AF_UNIX) if unix_path is not None else socket.create_server(("127.0.0.1", 0))
    with listener:
        if unix_path is not None:
            listener.bind(str(unix_path))
            listener.listen()
            url = "http://executor-host"
        else:
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(observe, lifespan="off", log_level="warning"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 5
            while not server.started:
                if monotonic() >= deadline:
                    pytest.fail("Uvicorn did not start")
                sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=6)
            assert not thread.is_alive(), "Uvicorn did not stop"


@pytest.fixture
def run_workspace(monkeypatch: pytest.MonkeyPatch, capture_root: Path) -> Iterator[Path]:
    with TemporaryDirectory(prefix="test-live-sse-", dir=capture_root) as path:
        workspace = Path(path)
        monkeypatch.setattr(dependencies, "CAPTURE_ROOT", workspace / "captures")
        monkeypatch.delenv("AGENT_LENS_EXECUTOR_SOCKET", raising=False)
        yield workspace


@pytest.fixture
def live_http() -> Iterator[tuple[httpx.Client, Event]]:
    request_finished = Event()
    with (
        running_server(app, request_finished) as url,
        httpx.Client(base_url=url, timeout=6, trust_env=False) as client,
    ):
        yield client, request_finished


@pytest.fixture
def run_config(run_workspace: Path) -> ReadOnlyRun:
    example = run_workspace / "example"
    example.mkdir()
    stub = run_workspace / "fake-codex"
    stub.write_text(f"#!{sys.executable}\n" + PROBE, encoding="utf-8")
    stub.chmod(0o700)
    return ReadOnlyRun(
        binary=stub, state_dir=run_workspace, workspace=example,
        model="test-model", api_key="not-a-real-key",
    )


@pytest.fixture
def executor_socket(
    monkeypatch: pytest.MonkeyPatch, run_workspace: Path, run_config: ReadOnlyRun,
) -> Iterator[Path]:
    unix_path = run_workspace / "executor.sock"
    monkeypatch.setattr(executor_app.state, "read_only_run", run_config, raising=False)
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(unix_path))
    with running_server(executor_app, Event(), unix_path=unix_path):
        yield unix_path


def next_frame(lines: Iterator[str]) -> tuple[str, dict[str, object]]:
    event_line = next(lines)
    data_line = next(lines)
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    assert next(lines) == ""
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return event_line.removeprefix("event: "), payload


def capture_directory(workspace: Path) -> Path:
    (directory,) = (workspace / "captures").glob("codex-prompt-*")
    return directory


@pytest.mark.parametrize("body", [{}, {"prompt": 42}, {"prompt": "  \n "}, {"prompt": "read", "workspace": "/"}])
def test_run_rejects_invalid_request_before_capture(
    body: dict[str, object],
    live_http: tuple[httpx.Client, Event],
    run_workspace: Path,
) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json=body)

    assert response.status_code == 422
    assert not (run_workspace / "captures").exists()


def test_run_requires_a_host_executor(live_http: tuple[httpx.Client, Event], run_workspace: Path) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json={"prompt": "Read the example."})

    assert response.status_code == 503
    assert response.json() == {"detail": "Host executor socket is not configured."}
    assert not (run_workspace / "captures").exists()


def test_run_rejects_unavailable_capture_directory(
    monkeypatch: pytest.MonkeyPatch, live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "unused.sock"))
    (run_workspace / "captures").touch()
    response = backend.post("/runs", json={"prompt": "Read the example."})

    assert response.status_code == 503
    assert response.json() == {"detail": "Capture directory is unavailable."}


def test_run_streams_saved_records_before_exit_and_rejects_a_concurrent_run(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
    run_config: ReadOnlyRun, executor_socket: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json={"prompt": LITERAL_PROMPT}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == "text/event-stream"
        frames = response.iter_lines()
        name, first = next_frame(frames)
        assert (name, first["event"]) == ("record", {
            "type": "turn.started", "prompt": LITERAL_PROMPT, "cwd": str(run_config.workspace),
        })
        directory = capture_directory(run_workspace)
        trace = directory / "events.timed.jsonl"
        assert json.loads(trace.read_text(encoding="utf-8")) == first
        assert not (directory / "result.json").exists()

        transport = httpx.HTTPTransport(uds=str(executor_socket))
        with httpx.Client(transport=transport, base_url="http://executor-host", timeout=2) as executor:
            assert executor.post("/run", json={"prompt": "second run"}).json() == {"exit_code": None}

        (run_workspace / "release").touch()
        name, second = next_frame(frames)
        assert (name, second["event"]) == ("record", {"type": "turn.completed"})
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] == [first, second]
    assert json.loads((directory / "result.json").read_text(encoding="utf-8")) == {"exit_code": 0}


@pytest.mark.usefixtures("executor_socket")
def test_run_reports_subprocess_startup_failure(
    live_http: tuple[httpx.Client, Event], run_config: ReadOnlyRun,
) -> None:
    backend, _ = live_http
    run_config.binary.unlink()
    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_records_and_reports_nonzero_exit(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json={"prompt": "exit 7"}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, record = next_frame(frames)
        assert kind == "record"
        assert next_frame(frames) == ("end", {"exit_code": 7})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    assert json.loads((directory / "events.timed.jsonl").read_text(encoding="utf-8")) == record
    assert json.loads((directory / "result.json").read_text(encoding="utf-8")) == {"exit_code": 7}


@pytest.mark.usefixtures("executor_socket")
def test_run_finishes_capture_after_the_client_disconnects(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[TimedEvent | None] = []

    class TrackingQueue(Queue[TimedEvent | None]):
        def put_nowait(self, item: TimedEvent | None) -> None:
            enqueued.append(item)
            super().put_nowait(item)

    monkeypatch.setattr(capture_run, "Queue", TrackingQueue)
    backend, request_finished = live_http
    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, first = next_frame(frames)
        assert kind == "record"
        assert not request_finished.is_set()

    assert request_finished.wait(timeout=3), "The server did not finish the disconnected request"
    directory = capture_directory(run_workspace)
    result = directory / "result.json"
    assert not result.exists()
    (run_workspace / "release").touch()

    deadline = monotonic() + 5
    while not result.exists() or not result.read_bytes().endswith(b"\n"):
        if monotonic() >= deadline:
            pytest.fail("The capture did not finish after disconnect")
        sleep(0.01)

    assert json.loads(result.read_text(encoding="utf-8")) == {"exit_code": 0}
    trace = directory / "events.timed.jsonl"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert records[0] == first
    assert records[1]["event"] == {"type": "turn.completed"}

    # Round-trip through the same event loop after the worker finished publishing.
    assert backend.get("/openapi.json").status_code == 200
    assert len(enqueued) == 1
    queued_record = enqueued[0]
    assert queued_record is not None
    assert queued_record.model_dump() == first


@pytest.mark.parametrize(
    "tail",
    [(), ({"exit_code": 0}, {"stdout": '{"type": "turn.completed"}'})],
    ids=["missing-exit-status", "stdout-after-exit-status"],
)
def test_run_keeps_observed_records_and_rejects_invalid_executor_streams(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    tail: tuple[dict[str, object], ...],
) -> None:
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
        backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response,
    ):
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, record = next_frame(frames)
        assert (kind, record["event"]) == ("record", event)
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    assert json.loads((directory / "events.timed.jsonl").read_text(encoding="utf-8")) == record
    assert not (directory / "result.json").exists()


def test_run_reports_unconfirmed_result_when_socket_is_unreachable(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "missing.sock"))
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    assert not (directory / "events.timed.jsonl").exists()
    assert not (directory / "result.json").exists()


@pytest.mark.usefixtures("executor_socket")
def test_executor_redacts_its_key_before_broadcast_and_persistence(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, run_config: ReadOnlyRun,
) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json={"prompt": "report a diagnostic"})

    assert response.status_code == 200
    assert run_config.api_key not in response.text
    frames = iter(response.text.splitlines())
    assert next_frame(frames)[0] == "record"
    kind, diagnostic = next_frame(frames)
    assert (kind, diagnostic["event"]) == ("record", {"type": "error", "message": "[REDACTED]"})
    assert next_frame(frames) == ("end", {"exit_code": 7})
    assert list(frames) == []

    trace = capture_directory(run_workspace) / "events.timed.jsonl"
    text = trace.read_text(encoding="utf-8")
    assert run_config.api_key not in text
    assert json.loads(text.splitlines()[-1]) == diagnostic


@pytest.mark.usefixtures("executor_socket")
def test_run_streams_large_event_intact(live_http: tuple[httpx.Client, Event], run_workspace: Path) -> None:
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "emit a big record"}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        records = []
        for expected_type in ("turn.started", "item.completed", "turn.completed"):
            kind, record = next_frame(frames)
            assert kind == "record"
            assert TimedEvent.model_validate(record).event.type == expected_type
            records.append(record)
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert records[1]["event"] == {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "agent_message", "text": "x" * (128 * 1024)},
    }
    directory = capture_directory(run_workspace)
    trace = (directory / "events.timed.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in trace.splitlines()] == records
    assert json.loads((directory / "result.json").read_text(encoding="utf-8")) == {"exit_code": 0}


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_trace_when_a_line_exceeds_the_limit(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "MAX_EVENT_BYTES", 32 * 1024)
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "emit a big record"}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, record = next_frame(frames)
        assert (kind, TimedEvent.model_validate(record).event.type) == ("record", "turn.started")
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "event_too_large"})
        assert list(frames) == []

    directory = capture_directory(run_workspace)
    trace = (directory / "events.timed.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in trace.splitlines()] == [record]
    assert not (directory / "result.json").exists()
