import json
import socket
import sys
from asyncio import Queue
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Response
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from starlette.types import ASGIApp, Receive, Scope, Send

import executor
from app import app
from executor import app as executor_app
from src.api import dependencies
from src.application import capture_run
from src.application.capture_run import FlushedRecord
from src.application.run_continuation import RunSession
from src.domain.trace_events import TimedEvent
from src.infrastructure.capture_files import CaptureFiles
from src.infrastructure.catalog_run import CatalogRun

LITERAL_PROMPT = '  --help\nLeia o README "juniper" ☕.\n'
PROBE = """
import json
import os
import sys
import time
from pathlib import Path

thread_id = "c47f8d99-9af1-4219-bc65-a55a27096cb8"
assert sys.argv[1:4] == ["app-server", "--listen", "stdio://"]
assert 'default_permissions="agent-lens-catalog"' in sys.argv
filesystem = next(arg for arg in sys.argv if arg.startswith('permissions.agent-lens-catalog.filesystem='))
assert json.dumps(str(Path.cwd())) + ' = "write"' in filesystem
assert 'permissions.agent-lens-catalog.network.enabled=false' in sys.argv
assert 'shell_environment_policy.inherit="none"' in sys.argv


def request(method):
    msg = json.loads(sys.stdin.readline())
    assert msg["method"] == method
    return msg


def reply(msg, result):
    print(json.dumps({"id": msg["id"], "result": result}), flush=True)


def notify(method, **params):
    print(json.dumps({"method": method, "params": params}), flush=True)


reply(request("initialize"), {})
request("initialized")
login = request("account/login/start")
assert login["params"]["type"] == "apiKey"
assert 'cli_auth_credentials_store="ephemeral"' in sys.argv
api_key = login["params"]["apiKey"]
assert api_key == "not-a-real-key"
assert not {"CODEX_API_KEY", "OPENAI_API_KEY"} & os.environ.keys()
reply(login, {"type": "apiKey"})
opening = json.loads(sys.stdin.readline())
resuming = opening["method"] == "thread/resume"
assert opening["method"] in {"thread/start", "thread/resume"}
assert opening["params"]["permissions"] == "agent-lens-catalog"
assert opening["params"]["approvalPolicy"] == "never"
assert opening["params"]["cwd"] == str(Path.cwd())
if resuming:
    assert opening["params"]["threadId"] == thread_id
    assert opening["params"]["excludeTurns"]
    opening_state = Path(os.environ["CODEX_HOME"])
    if (opening_state / "reject-resume").exists():
        with (opening_state / "resume-attempts").open("a") as attempts:
            print("reject the continuation", file=attempts)
        print(json.dumps({"id": opening["id"], "error": {"code": -32600}}), flush=True)
        raise SystemExit(1)
if not resuming:
    counter = Path(os.environ["CODEX_HOME"]) / "thread-started"
    if counter.exists():
        thread_id = "55555555-5555-4555-8555-555555555555"
    counter.touch()
reply(opening, {
    "thread": {"id": thread_id}, "cwd": str(Path.cwd()), "approvalPolicy": "never",
    "activePermissionProfile": {"id": "agent-lens-catalog"},
})
turn = request("turn/start")
assert turn["params"]["threadId"] == thread_id
prompt = turn["params"]["input"][0]["text"]
reply(turn, {"turn": {"id": "current-turn"}})


def emit(raw, *, flush):
    event = json.loads(raw)
    kind = event.pop("type")
    ids = {"threadId": thread_id, "turnId": "current-turn"}
    if kind == "turn.started":
        notify("turn/started", **ids, turn={"id": "current-turn"})
    elif kind in {"turn.completed", "turn.failed"}:
        status = "completed" if kind == "turn.completed" else "failed"
        notify("turn/completed", **ids, turn={"id": "current-turn", "status": status})
    elif kind.startswith("item."):
        def camel(key):
            first, *parts = key.split("_")
            return first + "".join(part.title() for part in parts)
        item = event["item"]
        fields = {camel(key): value for key, value in item.items()}
        fields["type"] = camel(item["type"])
        if fields.get("status") == "in_progress":
            fields["status"] = "inProgress"
        notify(kind.replace(".", "/"), **ids, item=fields)
    elif kind == "error":
        notify("error", **ids, error={"message": event["message"]}, willRetry=False)
    elif kind == "future.event":
        notify("item/completed", **ids, item={
            "type": "agentMessage", "id": f'item_{event["sequence"]}', "text": str(event["sequence"]),
        })


state = Path(os.environ["CODEX_HOME"])
marker_file = state / "session-marker"
if prompt in ("catalog first", "catalog second"):
    target = Path.cwd() / "catalog.txt"
    if prompt == "catalog first":
        target.write_text("category filtering\\n")
    else:
        assert target.read_text() == "category filtering\\n"
        target.write_text(target.read_text() + "pagination\\n")
    emit(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    emit(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Edit recorded"}}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "independent conversation":
    emit(json.dumps({"type": "thread.started", "thread_id": "55555555-5555-4555-8555-555555555555"}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "save marker: niobium":
    assert not resuming
    marker_file.write_text("niobium")
if prompt in ("recall the marker", "reject the continuation"):
    assert resuming
    with (state / "resume-attempts").open("a") as attempts:
        print(prompt, file=attempts)
    if prompt == "reject the continuation":
        raise SystemExit(7)
if prompt in ("save marker: niobium", "recall the marker"):
    emit(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    emit('{"type":"turn.started"}', flush=True)
    if resuming:
        for total, last in (
            ({"inputTokens": 110, "cachedInputTokens": 33, "outputTokens": 22},
             {"inputTokens": 10, "cachedInputTokens": 3, "outputTokens": 2}),
            ({"inputTokens": 125, "cachedInputTokens": 37, "outputTokens": 25},
             {"inputTokens": 15, "cachedInputTokens": 4, "outputTokens": 3}),
        ):
            notify("thread/tokenUsage/updated", threadId=thread_id, turnId="current-turn",
                   tokenUsage={"total": total, "last": last})
        emit(json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": marker_file.read_text(),
        }}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "report command outcome":
    time.sleep(1)
emit(json.dumps({"type": "turn.started", "prompt": prompt, "cwd": str(Path.cwd())}), flush=True)
if prompt == "report a diagnostic":
    emit(json.dumps({"type": "error", "message": api_key}), flush=True)
    emit('{"type":"turn.failed"}', flush=True)
    raise SystemExit(0)
if prompt == "emit a big record":
    emit(json.dumps({"type": "item.completed", "item": {
        "id": "item_1", "type": "agent_message", "text": "x" * (128 * 1024),
    }}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "report command outcome":
    emit(json.dumps({"type": "item.completed", "item": {
        "type": "command_execution", "status": "failed", "exit_code": 127,
        "command": "private-fake-command", "aggregated_output": "private-fake-stdout",
    }}), flush=True)
    notify("thread/tokenUsage/updated", threadId=thread_id, turnId="current-turn",
           tokenUsage={"total": {"inputTokens": 12, "cachedInputTokens": 4, "outputTokens": 3}})
    notify("item/completed", threadId="another-thread", turnId="another-turn",
           item={"type": "agentMessage", "id": "child-item", "text": "private-fake-event"})
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "measure command spans":
    def command(event_type, item_id, status, exit_code=None):
        emit(json.dumps({"type": event_type, "item": {
            "type": "command_execution", "id": item_id, "status": status, "exit_code": exit_code,
            "command": "private-command-text", "aggregated_output": "private-command-output",
        }}), flush=True)
    command("item.started", "item_1", "in_progress")
    time.sleep(0.03)
    command("item.started", "item_2", "in_progress")
    command("item.completed", "item_2", "failed", 127)
    time.sleep(0.04)
    command("item.completed", "item_1", "completed", 0)
    command("item.completed", "item_3", "completed", 0)
    command("item.started", "item_4", "in_progress")
    command("item.started", "private-invalid-id", "in_progress")
    command("item.completed", "private-invalid-id", "completed", 0)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "exit 7":
    raise SystemExit(7)
if prompt == "stream across reconnect":
    state = Path(os.environ["CODEX_HOME"])
    def wait_for(marker):
        deadline = time.monotonic() + 10
        while not (state / marker).exists():
            if time.monotonic() >= deadline:
                raise SystemExit(8)
            time.sleep(0.01)

    for sequence in range(3, 34):
        emit(json.dumps({"type": "future.event", "sequence": sequence}), flush=True)
    wait_for("emit-34")
    emit(json.dumps({"type": "future.event", "sequence": 34}), flush=True)
    wait_for("emit-35")
    emit(json.dumps({"type": "future.event", "sequence": 35}), flush=True)
    wait_for("finish-run")
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt.startswith("  --help"):
    assert prompt == '  --help\\nLeia o README "juniper" ☕.\\n'
release = Path(os.environ["CODEX_HOME"]) / "release"
deadline = time.monotonic() + 5
while not release.exists():
    if time.monotonic() >= deadline:
        raise SystemExit(8)
    time.sleep(0.01)
emit('{"type":"turn.completed"}', flush=True)
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
def run_config(run_workspace: Path) -> CatalogRun:
    example = run_workspace / "example"
    example.mkdir()
    stub = run_workspace / "fake-codex"
    stub.write_text(f"#!{sys.executable}\n" + PROBE, encoding="utf-8")
    stub.chmod(0o700)
    return CatalogRun(
        binary=stub, state_dir=run_workspace, workspace=example,
        model="test-model", api_key="not-a-real-key",
    )


@pytest.fixture
def executor_socket(
    monkeypatch: pytest.MonkeyPatch, run_workspace: Path, run_config: CatalogRun,
) -> Iterator[Path]:
    unix_path = run_workspace / "executor.sock"
    monkeypatch.setattr(executor_app.state, "catalog_run", run_config, raising=False)
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


@pytest.mark.usefixtures("executor_socket")
def test_run_stream_bridges_history_and_live_capture(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json={"prompt": "stream across reconnect"}) as response:
        assert response.status_code == 200
        run_id = response.headers["X-Run-ID"]
        original = response.iter_lines()
        assert [next_frame(original)[0] for _ in range(33)] == ["record"] * 33

    directory = capture_directory(run_workspace)
    assert run_id == directory.name
    run_url = f"/runs/{run_id}"
    history = backend.get(run_url + "/events")
    assert history.status_code == 200
    assert history.json()["latest_event_id"] == 33
    assert backend.post(run_url + "/continue", json={"prompt": "continue now"}).status_code == 409
    (run_workspace / "emit-34").touch()

    trace = directory / "events.timed.jsonl"
    deadline = monotonic() + 3
    while b'"text": "34"' not in trace.read_bytes():
        if monotonic() >= deadline:
            pytest.fail("Event 34 was not saved before connecting")
        sleep(0.01)

    with backend.stream("GET", run_url + "/stream", params={"after": 33}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == "text/event-stream"
        frames = response.iter_lines()
        assert next(frames) == "id: 34"
        kind, record = next_frame(frames)
        assert (kind, record["event"]) == ("record", {
            "type": "item.completed", "item": {"type": "agent_message", "id": "item_34", "text": "34"},
        })

        (run_workspace / "emit-35").touch()
        assert next(frames) == "id: 35"
        kind, record = next_frame(frames)
        assert (kind, record["event"]) == ("record", {
            "type": "item.completed", "item": {"type": "agent_message", "id": "item_35", "text": "35"},
        })
        assert not (directory / "result.json").exists()

        (run_workspace / "finish-run").touch()
        assert next(frames) == "id: 36"
        assert next_frame(frames)[1]["event"] == {"type": "turn.completed"}
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0
    assert len(trace.read_text(encoding="utf-8").splitlines()) == 36
    assert capture_directory(run_workspace) == directory


@pytest.mark.usefixtures("executor_socket")
def test_latest_run_resumes_the_thread_once_and_links_survive_api_restart(run_workspace: Path) -> None:
    thread_id = "c47f8d99-9af1-4219-bc65-a55a27096cb8"
    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as backend:
        first = backend.post("/runs", json={"prompt": "save marker: niobium"})
        first_id = first.headers["X-Run-ID"]
        original = f"/runs/{first_id}"
        assert thread_id in first.text
        assert backend.get(original + "/events").json()["continuation"]["state"] == "available"
        assert backend.post(original + "/continue", json={"prompt": "hello", "thread_id": thread_id}).status_code == 422

        with ThreadPoolExecutor(max_workers=2) as pool:
            sends = [pool.submit(backend.post, original + "/continue", json={"prompt": "recall the marker"})
                     for _ in range(2)]
            accepted, rejected = sorted((send.result() for send in sends), key=lambda response: response.status_code)

        assert (accepted.status_code, rejected.status_code) == (200, 409)
        assert "X-Run-ID" not in rejected.headers
        second_id = accepted.headers["X-Run-ID"]
        assert second_id != first_id
        assert thread_id in accepted.text
        assert "niobium" in accepted.text
        records = [json.loads(line[6:]) for line in accepted.text.splitlines() if line.startswith("data: ")]
        assert records[-2]["event"]["usage"] == {"input_tokens": 25, "cached_input_tokens": 7, "output_tokens": 5}
        assert (run_workspace / "resume-attempts").read_text().splitlines() == ["recall the marker"]
        assert len(list((run_workspace / "captures").iterdir())) == 2
        first_session = CaptureFiles.find(run_workspace / "captures", first_id).read_session()
        second_session = CaptureFiles.find(run_workspace / "captures", second_id).read_session()
        assert first_session is not None and second_session is not None
        assert first_session.prompt == "save marker: niobium"
        assert second_session.prompt == "recall the marker"
        assert first_session.submitted_at is not None and second_session.submitted_at is not None
        assert first_session.submitted_at.tzinfo == UTC
        assert second_session.submitted_at.tzinfo == UTC
        listing = backend.get("/threads")
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        assert len(listing.json()["threads"]) == 1
        summary = listing.json()["threads"][0]
        assert summary["thread_id"] == thread_id
        assert summary["root_run_id"] == first_id
        assert summary["latest_run_id"] == second_id
        detail = backend.get(f"/threads/by-run/{second_id}").json()
        assert [turn["prompt"] for turn in detail["turns"]] == ["save marker: niobium", "recall the marker"]
        assert detail["turns"][1]["assistant_markdown"] == "niobium"
        assert [turn["summary"]["actions"] for turn in detail["turns"]] == [0, 0]
        assert all(turn["summary"]["failures"] == 0 for turn in detail["turns"])
        assert backend.get(f"/threads/{thread_id}").json() == detail
        independent = backend.post("/runs", json={"prompt": "independent conversation"})
        independent_id = independent.headers["X-Run-ID"]
        listing = backend.get("/threads").json()["threads"]
        assert [row["root_run_id"] for row in listing] == [independent_id, first_id]

    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as backend:
        restored = backend.get("/threads").json()["threads"]
        assert len(restored) == 2
        assert restored[1]["root_run_id"] == first_id
        assert restored[1]["latest_run_id"] == second_id
        assert backend.get(f"/threads/{thread_id}").json()["turns"] == detail["turns"]
        old = backend.get(f"/runs/{first_id}/events").json()
        new = backend.get(f"/runs/{second_id}/events").json()
        assert old["continuation"]["state"] == "superseded"
        assert old["continuation"]["latest_run_id"] == second_id
        assert new["continuation"]["previous_run_id"] == first_id
        assert new["continuation"]["state"] == "available"
        for page in (old, new):
            assert page["events"][0]["record"]["event"]["thread_id"] == thread_id


@pytest.mark.usefixtures("executor_socket")
def test_controlled_turns_edit_same_workspace(live_http: tuple[httpx.Client, Event], run_workspace: Path) -> None:
    backend, _ = live_http
    first = backend.post("/runs", json={"prompt": "catalog first"})
    first_id = first.headers["X-Run-ID"]
    second = backend.post(f"/runs/{first_id}/continue", json={"prompt": "catalog second"})
    second_id = second.headers["X-Run-ID"]
    assert (run_workspace / "example" / "catalog.txt").read_text() == "category filtering\npagination\n"
    detail = backend.get(f"/threads/by-run/{second_id}").json()
    assert [turn["run_id"] for turn in detail["turns"]] == [first_id, second_id]
    assert detail["thread_id"] == "c47f8d99-9af1-4219-bc65-a55a27096cb8"


@pytest.mark.usefixtures("executor_socket")
def test_continuation_rejects_legacy_and_reopens_failed_attempt_without_events(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    legacy = CaptureFiles.create(run_workspace / "captures", prefix="codex-prompt-")
    legacy.ensure_trace()
    legacy.write_result(0)
    assert backend.get(f"/runs/{legacy.directory.name}/events").json()["continuation"]["state"] == "ephemeral"
    assert backend.post(f"/runs/{legacy.directory.name}/continue", json={"prompt": "hello"}).status_code == 409
    assert backend.post("/runs/missing/continue", json={"prompt": "hello"}).status_code == 404

    first = backend.post("/runs", json={"prompt": "save marker: niobium"})
    first_id = first.headers["X-Run-ID"]
    (run_workspace / "reject-resume").touch()
    failed = backend.post(f"/runs/{first_id}/continue", json={"prompt": "reject the continuation"})
    assert failed.status_code == 200
    failed_id = failed.headers["X-Run-ID"]
    response = backend.get(f"/runs/{failed_id}/events")
    assert response.status_code == 200
    page = response.json()
    assert page["events"] == []
    assert page["status"] == "failed"
    assert page["continuation"]["state"] == "unavailable"
    assert page["continuation"]["previous_run_id"] == first_id
    assert backend.post(f"/runs/{failed_id}/continue", json={"prompt": "again"}).status_code == 409


def test_a_thread_id_from_an_older_executor_does_not_make_a_saved_run_resumable(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    capture = CaptureFiles.create(run_workspace / "captures", prefix="codex-prompt-")
    thread_id = UUID("c47f8d99-9af1-4219-bc65-a55a27096cb8")
    capture.write_session(RunSession(persistent=True))
    with capture.open_trace() as trace:
        trace.write('{"elapsed_ns":0,"event":{"type":"turn.completed"}}\n')
    capture.write_result(0, thread_id=thread_id)

    url = f"/runs/{capture.directory.name}"
    history = backend.get(url + "/events").json()
    assert history["status"] == "completed"
    assert history["continuation"]["state"] == "unavailable"
    assert backend.post(url + "/continue", json={"prompt": "try again"}).status_code == 409


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
    run_config: CatalogRun, executor_socket: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json={"prompt": LITERAL_PROMPT}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == "text/event-stream"
        frames = response.iter_lines()
        name, first = next_frame(frames)
        assert (name, TimedEvent.model_validate(first).event.type) == ("record", "thread.started")
        _, started = next_frame(frames)
        assert started["event"] == {"type": "turn.started"}
        directory = capture_directory(run_workspace)
        trace = directory / "events.timed.jsonl"
        assert [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] == [first, started]
        assert not (directory / "result.json").exists()

        transport = httpx.HTTPTransport(uds=str(executor_socket))
        with httpx.Client(transport=transport, base_url="http://executor-host", timeout=2) as executor:
            assert executor.post("/run", json={"prompt": "second run"}).json() == {"exit_code": None}

        (run_workspace / "release").touch()
        name, second = next_frame(frames)
        assert (name, second["event"]) == ("record", {"type": "turn.completed"})
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] == [first, started, second]
    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0


@pytest.mark.usefixtures("executor_socket")
def test_run_reports_subprocess_startup_failure(
    live_http: tuple[httpx.Client, Event], run_config: CatalogRun,
) -> None:
    backend, _ = live_http
    run_config.binary.unlink()
    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        assert next_frame(frames) == ("end", {"exit_code": None, "error": "execution_failed"})
        assert list(frames) == []


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_records_when_app_server_exits_before_turn_completion(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json={"prompt": "exit 7"}) as response:
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


@pytest.mark.usefixtures("executor_socket")
def test_run_finishes_capture_after_the_client_disconnects(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    enqueued: list[FlushedRecord | None] = []

    class TrackingQueue(Queue[FlushedRecord | None]):
        def put_nowait(self, item: FlushedRecord | None) -> None:
            enqueued.append(item)
            super().put_nowait(item)

    monkeypatch.setattr(capture_run, "Queue", TrackingQueue)
    backend, request_finished = live_http
    span_exporter, log_exporter = telemetry_exports
    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        kind, first = next_frame(frames)
        assert kind == "record"
        assert next_frame(frames)[1]["event"] == {"type": "turn.started"}
        assert not request_finished.is_set()

    assert request_finished.wait(timeout=3), "The server did not finish the disconnected request"
    directory = capture_directory(run_workspace)
    result = directory / "result.json"
    assert not result.exists()
    assert [span.name for span in span_exporter.get_finished_spans()] == ["run.sse", "POST /runs"]
    (run_workspace / "release").touch()

    deadline = monotonic() + 5
    while not result.exists() or not result.read_bytes().endswith(b"\n"):
        if monotonic() >= deadline:
            pytest.fail("The capture did not finish after disconnect")
        sleep(0.01)

    assert json.loads(result.read_text(encoding="utf-8"))["exit_code"] == 0
    trace = directory / "events.timed.jsonl"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert records[0] == first
    assert records[2]["event"] == {"type": "turn.completed"}

    # Round-trip through the same event loop after the worker finished publishing.
    assert backend.get("/openapi.json").status_code == 200
    assert len(enqueued) == 2
    queued_record = enqueued[0]
    assert queued_record is not None
    assert queued_record.record.model_dump() == first
    spans = assert_live_span_tree(span_exporter, directory.name)
    assert (spans["POST /runs"].attributes or {})["agent_lens.outcome"] == "disconnected"
    assert (spans["run.capture"].attributes or {})["agent_lens.outcome"] == "completed"
    sse_attributes = spans["run.sse"].attributes or {}
    assert sse_attributes["agent_lens.outcome"] == "disconnected"
    assert sse_attributes["agent_lens.sse.records_sent"] == 2
    assert [event.name for event in spans["run.sse"].events] == ["run.sse.first_record_sent"]
    sse_end = spans["run.sse"].end_time
    write_start = spans["capture.write_result"].start_time
    assert sse_end is not None and write_start is not None and sse_end < write_start
    assert (spans["capture.write_result"].attributes or {})["agent_lens.outcome"] == "completed"
    assert_correlated_capture_logs(log_exporter, run_workspace, directory.name)


def assert_live_span_tree(span_exporter: InMemorySpanExporter, run_id: str) -> dict[str, ReadableSpan]:
    names = ["POST /runs", "run.capture", "executor.request", "POST /run", "codex.subprocess"]
    extra = [("POST /runs", "run.sse"), ("run.capture", "capture.write_result")]
    deadline = monotonic() + 5
    while len(span_exporter.get_finished_spans()) < len(names) + len(extra):
        if monotonic() >= deadline:
            pytest.fail("The correlated capture did not finish")
        sleep(0.01)
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    assert set(spans) == set(names) | {child for _, child in extra}
    assert len({span.context.trace_id for span in spans.values() if span.context}) == 1
    assert {(span.attributes or {})["agent_lens.run_id"] for span in spans.values()} == {run_id}
    for parent, child in [*pairwise(names), *extra]:
        parent_context, child_parent = spans[parent].context, spans[child].parent
        assert parent_context is not None and child_parent is not None
        assert child_parent.span_id == parent_context.span_id
    return spans


def assert_correlated_capture_logs(log_exporter: InMemoryLogRecordExporter, run_workspace: Path, run_id: str) -> None:
    logs = {item.log_record.body: item.log_record for item in log_exporter.get_finished_logs()}
    events = ("run.accepted", "run.sse.disconnected", "executor.subprocess.started", "run.capture.completed")
    assert len({logs[event].trace_id for event in events}) == 1
    assert all((logs[event].attributes or {})["run_id"] == run_id for event in events)
    sent = [item for item in log_exporter.get_finished_logs() if item.log_record.body == "run.sse.record_sent"]
    assert [(item.log_record.attributes or {})["event_count"] for item in sent] == [1, 2]
    assert (logs["run.capture.completed"].attributes or {})["event_count"] == 3
    assert "run.sse.end_sent" not in logs
    assert str(run_workspace) not in str(logs)
    assert "Read the example." not in str(logs)

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
    assert json.loads((directory / "result.json").read_text())["exit_code"] is None


def test_run_reports_unconfirmed_result_when_socket_is_unreachable(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "missing.sock"))
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "Read the example."}) as response:
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
    assert detail.json()["turns"][0]["status"] == "failed"


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
def test_executor_redacts_its_key_before_broadcast_and_persistence(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, run_config: CatalogRun,
) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json={"prompt": "report a diagnostic"})

    assert response.status_code == 200
    assert run_config.api_key not in response.text
    frames = iter(response.text.splitlines())
    assert next_frame(frames)[0] == "record"
    assert next_frame(frames)[0] == "record"
    kind, diagnostic = next_frame(frames)
    assert (kind, diagnostic["event"]) == ("record", {"type": "error", "message": "[REDACTED]"})
    assert TimedEvent.model_validate(next_frame(frames)[1]).event.type == "turn.failed"
    assert next_frame(frames) == ("end", {"exit_code": 1})
    assert list(frames) == []

    trace = capture_directory(run_workspace) / "events.timed.jsonl"
    text = trace.read_text(encoding="utf-8")
    assert run_config.api_key not in text
    assert json.loads(text.splitlines()[-2]) == diagnostic


@pytest.mark.usefixtures("executor_socket")
def test_run_streams_large_event_intact(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "emit a big record"}) as response:
        assert response.status_code == 200
        frames = response.iter_lines()
        records = []
        for expected_type in ("thread.started", "turn.started", "item.completed", "turn.completed"):
            kind, record = next_frame(frames)
            assert kind == "record"
            assert TimedEvent.model_validate(record).event.type == expected_type
            records.append(record)
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert records[2]["event"] == {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "agent_message", "text": "x" * (128 * 1024)},
    }
    directory = capture_directory(run_workspace)
    trace = (directory / "events.timed.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in trace.splitlines()] == records
    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0

    _, log_exporter = telemetry_exports
    stderr = capsys.readouterr().err
    exported = [item.log_record.attributes or {} for item in log_exporter.get_finished_logs()]
    for logs in (exported, [json.loads(line) for line in stderr.splitlines()]):
        assert_live_log_milestones(logs, directory.name)
    for private_data in ("emit a big record", "x" * 128, "not-a-real-key", str(run_workspace)):
        assert private_data not in stderr
        assert private_data not in str(exported)


def assert_live_log_milestones(logs: Sequence[Mapping[str, object]], run_id: str) -> None:
    milestones = (
        "executor.request.started",
        "executor.subprocess.first_event_read",
        "executor.request.first_event_received",
        "run.capture.first_record_saved",
        "run.sse.first_record_sent",
    )
    assert tuple(log["event"] for log in logs if log["event"] in milestones) == milestones
    assert {log["run_id"] for log in logs} == {run_id}
    assert len({log["trace_id"] for log in logs}) == 1
    by_event = {log["event"]: log for log in logs}
    assert all(isinstance(by_event[name]["duration_ms"], int) for name in milestones[1:])
    assert by_event["executor.subprocess.completed"]["event_count"] == 4
    assert by_event["run.capture.completed"]["event_count"] == 4

    delivery = [log for log in logs if log["event"] in ("run.sse.record_sent", "run.sse.end_sent")]
    assert [(log["event"], log["event_count"]) for log in delivery] == [
        ("run.sse.record_sent", 1), ("run.sse.record_sent", 2), ("run.sse.record_sent", 3),
        ("run.sse.record_sent", 4), ("run.sse.end_sent", 4),
    ]
    assert by_event["run.sse.end_sent"]["exit_code"] == 0


@pytest.mark.usefixtures("executor_socket")
def test_run_logs_safe_command_metadata_for_a_failed_command_in_a_completed_turn(
    live_http: tuple[httpx.Client, Event],
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
    capsys: pytest.CaptureFixture[str], run_workspace: Path,
) -> None:
    backend, request_finished = live_http
    response = backend.post("/runs", json={"prompt": "report command outcome"})
    assert response.status_code == 200
    assert "private-fake-command" in response.text
    assert "private-fake-event" not in response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-2]["event"]["usage"] == {"input_tokens": 12, "cached_input_tokens": 4, "output_tokens": 3}
    assert request_finished.wait(3)

    span_exporter, log_exporter = telemetry_exports
    spans = assert_live_span_tree(span_exporter, capture_directory(run_workspace).name)
    assert_successful_stream_spans(spans)
    sse_context = spans["run.sse"].context
    assert sse_context is not None
    stderr = capsys.readouterr().err
    exported = [item.log_record.attributes or {} for item in log_exporter.get_finished_logs()]
    for logs in (exported, [json.loads(line) for line in stderr.splitlines()]):
        records = [log for log in logs if log["event"] == "run.sse.record_sent"]
        assert [log["event_type"] for log in records] == ["thread.started", "turn.started", "item.completed", "turn.completed"]
        command = records[2]
        assert command["item_type"] == "command_execution"
        assert command["command_status"] == "failed"
        assert command["command_exit_code"] == 127
        assert command["outcome"] == "completed"
        delays = [delay for log in records if isinstance(delay := log["flush_to_sse_us"], int)]
        assert len(delays) == len(records)
        assert max(delays) == (spans["run.sse"].attributes or {})["agent_lens.sse.max_flush_to_sse_us"]
        assert {log["span_id"] for log in records} == {f"{sse_context.span_id:016x}"}
    for private_data in ("private-fake-command", "private-fake-stdout", "private-fake-event"):
        assert private_data not in stderr
        assert private_data not in str(exported)


def assert_successful_stream_spans(spans: dict[str, ReadableSpan]) -> None:
    subprocess_span = spans["codex.subprocess"]
    assert [event.name for event in subprocess_span.events] == [
        "executor.subprocess.first_event_read", "executor.stream.first_event_sent",
    ]
    first_read, first_sent = subprocess_span.events
    assert subprocess_span.start_time is not None
    assert first_read.timestamp >= subprocess_span.start_time
    assert first_read.timestamp <= first_sent.timestamp

    request_events = spans["executor.request"].events
    assert [event.name for event in request_events] == [
        "executor.request.first_event_received", "executor.request.terminal_received",
    ]
    first_received, terminal_received = request_events
    assert first_read.timestamp <= first_received.timestamp <= terminal_received.timestamp

    sse_span = spans["run.sse"]
    assert [event.name for event in sse_span.events] == ["run.sse.first_record_sent", "run.sse.end_sent"]
    assert (sse_span.attributes or {})["agent_lens.sse.records_sent"] == 4
    assert (sse_span.attributes or {})["agent_lens.outcome"] == "completed"
    assert (spans["capture.write_result"].attributes or {})["agent_lens.outcome"] == "completed"


@pytest.mark.usefixtures("executor_socket")
def test_run_traces_result_write_failure_without_raw_exception(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    def fail_write(
        self: CaptureFiles, exit_code: int | None, *, thread_id: UUID | None = None, persistence_confirmed: bool = False,
        **kwargs: object,
    ) -> None:
        raise OSError("private-result-path")

    monkeypatch.setattr(CaptureFiles, "write_result", fail_write)
    (run_workspace / "release").touch()
    backend, request_finished = live_http
    response = backend.post("/runs", json={"prompt": "Read the example."})
    assert response.status_code == 200
    assert next_frame(iter(response.text.splitlines()[-3:])) == (
        "end", {"exit_code": None, "error": "execution_failed"},
    )
    assert request_finished.wait(3)

    directory = capture_directory(run_workspace)
    assert (directory / "events.timed.jsonl").is_file()
    assert not (directory / "result.json").exists()
    span_exporter, log_exporter = telemetry_exports
    spans = assert_live_span_tree(span_exporter, directory.name)
    write_result = spans["capture.write_result"]
    assert (write_result.attributes or {})["agent_lens.error_code"] == "capture_result_write_failed"
    assert write_result.status.status_code is StatusCode.ERROR
    assert not write_result.events
    assert "private-result-path" not in str(log_exporter.get_finished_logs())
    assert (spans["run.capture"].attributes or {})["agent_lens.outcome"] == "failed"
    assert (spans["run.sse"].attributes or {})["agent_lens.outcome"] == "completed"


@pytest.mark.usefixtures("executor_socket")
def test_run_traces_paired_commands_at_their_observed_capture_offsets(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    backend, request_finished = live_http
    response = backend.post("/runs", json={"prompt": "measure command spans"})
    assert response.status_code == 200
    assert next_frame(iter(response.text.splitlines()[-3:])) == ("end", {"exit_code": 0})
    assert request_finished.wait(3)

    directory = capture_directory(run_workspace)
    lines = (directory / "events.timed.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    offsets = {
        (record["event"]["item"]["id"], record["event"]["type"]): record["elapsed_ns"]
        for record in records if "item" in record["event"]
    }

    span_exporter, _ = telemetry_exports
    spans = span_exporter.get_finished_spans()
    request_span = next(span for span in spans if span.name == "executor.request")
    assert request_span.context is not None
    attrs = request_span.attributes or {}
    assert attrs["agent_lens.command.missing_start_count"] == 1
    assert attrs["agent_lens.command.missing_completion_count"] == 1
    commands = [span for span in spans if span.name == "codex.command"]
    assert len(commands) == 2
    durations = {
        "item_1": offsets["item_1", "item.completed"] - offsets["item_1", "item.started"],
        "item_2": offsets["item_2", "item.completed"] - offsets["item_2", "item.started"],
    }
    assert durations["item_1"] > durations["item_2"]
    assert_command_span(commands[0], "item_2", durations["item_2"], request_span, "failed", 127)
    assert_command_span(commands[1], "item_1", durations["item_1"], request_span, "completed", 0)
    assert request_span.status.status_code is StatusCode.UNSET


def assert_command_span(
    span: ReadableSpan, item_id: str, duration_ns: int, request_span: ReadableSpan, status: str, exit_code: int,
) -> None:
    assert request_span.context is not None and span.context is not None and span.parent is not None
    assert span.context.trace_id == request_span.context.trace_id
    assert span.parent.span_id == request_span.context.span_id
    assert span.start_time is not None and span.end_time is not None
    assert span.end_time - span.start_time == duration_ns
    attrs = span.attributes or {}
    assert attrs["agent_lens.item_id"] == item_id
    assert attrs["agent_lens.command.status"] == status
    assert attrs["agent_lens.command.exit_code"] == exit_code
    assert attrs["agent_lens.outcome"] == status
    assert span.status.status_code is (StatusCode.ERROR if exit_code else StatusCode.UNSET)
    assert "private-command" not in str(attrs)
    assert "private-invalid-id" not in str(attrs)


@pytest.mark.usefixtures("executor_socket")
def test_run_preserves_trace_when_a_line_exceeds_the_limit(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "MAX_EVENT_BYTES", 32 * 1024)
    backend, _ = live_http

    with backend.stream("POST", "/runs", json={"prompt": "emit a big record"}) as response:
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


def test_replay_logs_disconnect_after_loading_a_valid_trace(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    trace = run_workspace / "replay.jsonl"
    records = [
        {"elapsed_ns": 0, "event": {"type": "turn.started"}},
        {"elapsed_ns": 30_000_000_000, "event": {"type": "turn.completed"}},
    ]
    trace.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setattr(dependencies, "REPLAY_TRACE", trace)
    backend, request_finished = live_http
    with backend.stream("GET", "/events") as response:
        assert next_frame(response.iter_lines()) == ("record", records[0])
    assert request_finished.wait(3)

    span_exporter, log_exporter = telemetry_exports
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    assert (spans["replay.load"].attributes or {})["agent_lens.outcome"] == "completed"
    assert (spans["replay.emit"].attributes or {})["agent_lens.outcome"] == "disconnected"
    assert {item.log_record.body for item in log_exporter.get_finished_logs()} == {
        "replay.load.completed", "replay.sse.disconnected",
    }
