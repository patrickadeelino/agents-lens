"""Stream a live run while it is being saved."""

import json
from asyncio import Queue
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.backend.application.capture import capture_run
from src.backend.application.capture.capture_run import FlushedRecord
from src.backend.main import app
from src.executor.agents.codex.catalog_run import CatalogRun
from src.shared.trace_events import TimedEvent
from tests.integration.end_to_end.support import (
    LITERAL_PROMPT,
    capture_directory,
    next_frame,
    prompt_body,
    running_server,
)
from tests.integration.end_to_end.telemetry_assertions import (
    assert_correlated_capture_logs,
    assert_live_log_milestones,
    assert_live_span_tree,
)


@pytest.mark.usefixtures("executor_socket")
def test_run_stream_bridges_history_and_live_capture(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json=prompt_body("stream across reconnect")) as response:
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
    (run_workspace / "state" / "emit-34").touch()

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

        (run_workspace / "state" / "emit-35").touch()
        assert next(frames) == "id: 35"
        kind, record = next_frame(frames)
        assert (kind, record["event"]) == ("record", {
            "type": "item.completed", "item": {"type": "agent_message", "id": "item_35", "text": "35"},
        })
        assert not (directory / "result.json").exists()

        (run_workspace / "state" / "finish-run").touch()
        assert next(frames) == "id: 36"
        assert next_frame(frames)[1]["event"] == {"type": "turn.completed"}
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0
    assert len(trace.read_text(encoding="utf-8").splitlines()) == 36
    assert capture_directory(run_workspace) == directory


def test_run_streams_saved_records_before_exit_and_rejects_a_concurrent_run(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
    run_config: CatalogRun, executor_socket: Path,
) -> None:
    backend, _ = live_http
    with backend.stream("POST", "/runs", json=prompt_body(LITERAL_PROMPT)) as response:
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
            assert executor.post("/run", json={"prompt": "second run", "workspace": str(run_config.workspace)}).json() == {"exit_code": None}

        (run_workspace / "state" / "release").touch()
        name, second = next_frame(frames)
        assert (name, second["event"]) == ("record", {"type": "turn.completed"})
        assert next_frame(frames) == ("end", {"exit_code": 0})
        assert list(frames) == []

    assert [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] == [first, started, second]
    assert json.loads((directory / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0


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
    with backend.stream("POST", "/runs", json=prompt_body("measure total duration")) as response:
        assert response.status_code == 200
        run_id = response.headers["X-Run-ID"]
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
    (run_workspace / "state" / "release").touch()

    deadline = monotonic() + 5
    while not result.exists() or not result.read_bytes().endswith(b"\n"):
        if monotonic() >= deadline:
            pytest.fail("The capture did not finish after disconnect")
        sleep(0.01)

    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved["exit_code"] == 0
    summary = saved["summary"]
    assert summary["total_duration_ns"] - summary["observed_duration_ns"] >= 300_000_000
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
    thread = backend.get(f"/threads/by-run/{run_id}").json()
    assert thread["turns"][0]["summary"]["total_duration_ns"] == summary["total_duration_ns"]


@pytest.mark.usefixtures("executor_socket")
def test_executor_redacts_its_key_before_broadcast_and_persistence(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, run_config: CatalogRun,
) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json=prompt_body("report a diagnostic"))

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

    with backend.stream("POST", "/runs", json=prompt_body("emit a big record")) as response:
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


@pytest.mark.usefixtures("executor_socket")
def test_delegation_reconnect_preserves_attribution_and_parent_outcome(run_workspace: Path) -> None:
    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as api:
        with api.stream("POST", "/runs", json=prompt_body("delegation capture")) as response:
            run_id = response.headers["X-Run-ID"]
            frames = response.iter_lines()
            first = [next_frame(frames)[1] for _ in range(4)]
        run_url = f"/runs/{run_id}"
        (run_workspace / "state" / "delegation-resume").touch()
        stream = api.get(run_url + "/stream", params={"after": 4})
        lines = stream.text.splitlines()
        tail = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
        ids = [int(line[4:]) for line in lines if line.startswith("id: ")]
        assert tail.pop() == {"exit_code": 0}
        assert ids == list(range(5, 5 + len(tail)))
    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as api:
        history = api.get(run_url + "/events").json()
        assert [row["record"] for row in history["events"]] == first + tail
        assert history["status"] == "completed"
        assert history["continuation"]["state"] == "available"

    events = [TimedEvent.model_validate(row).event.model_dump() for row in first + tail]
    assert [event["type"] for event in events[:4]] == [
        "thread.started", "turn.started", "delegation.event", "delegation.edge",
    ]
    assert len(list((run_workspace / "captures").iterdir())) == 1
    assert [(event["child_thread_id"], event["child_index"]) for event in events
            if event["type"] == "delegation.edge"] == [("child-1", 1), ("child-2", 2), ("nested-child", 3)]
    assert "unrelated-output" not in json.dumps(events)
    assert [(e["thread_id"], e["observation"]["item"]["aggregated_output"]) for e in events
            if e.get("observation", {}).get("item", {}).get("type") == "command_execution"] == [
        ("child-1", "child-output"), ("child-2", "sibling-output"), ("child-1", "conflicting-output"),
    ]
    assert {e["thread_id"] for e in events if e["type"] == "delegation.incomplete"} == {"child-2", "nested-child"}
    assert [e["item"]["text"] for e in events if e["type"] == "item.completed"] == ["root-only"]
