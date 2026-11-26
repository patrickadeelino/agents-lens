"""Check the spans and logs a live run leaves behind."""

from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from time import monotonic, sleep

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode


def assert_live_span_tree(span_exporter: InMemorySpanExporter, run_id: str) -> dict[str, ReadableSpan]:
    names = ["POST /runs", "run.capture", "executor.request", "POST /run", "agent.subprocess"]
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


def assert_successful_stream_spans(spans: dict[str, ReadableSpan]) -> None:
    subprocess_span = spans["agent.subprocess"]
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
