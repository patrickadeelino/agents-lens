"""Describe a run in logs and spans without leaking its content."""

import json
from pathlib import Path
from threading import Event
from uuid import UUID

import httpx
import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from src.backend.infrastructure.storage.capture_files import CaptureFiles
from tests.integration.end_to_end.support import (
    capture_directory,
    next_frame,
    prompt_body,
)
from tests.integration.end_to_end.telemetry_assertions import (
    assert_command_span,
    assert_live_span_tree,
    assert_successful_stream_spans,
)


@pytest.mark.usefixtures("executor_socket")
def test_run_logs_safe_command_metadata_for_a_failed_command_in_a_completed_turn(
    live_http: tuple[httpx.Client, Event],
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
    capsys: pytest.CaptureFixture[str], run_workspace: Path,
) -> None:
    backend, request_finished = live_http
    response = backend.post("/runs", json=prompt_body("report command outcome"))
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


@pytest.mark.usefixtures("executor_socket")
def test_run_traces_result_write_failure_without_raw_exception(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    def fail_write(
        self: CaptureFiles, exit_code: int | None, *, thread_id: UUID | None = None,
        **kwargs: object,
    ) -> None:
        raise OSError("private-result-path")

    monkeypatch.setattr(CaptureFiles, "write_result", fail_write)
    (run_workspace / "state" / "release").touch()
    backend, request_finished = live_http
    response = backend.post("/runs", json=prompt_body("Read the example."))
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
    response = backend.post("/runs", json=prompt_body("measure command spans"))
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
