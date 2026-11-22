import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import app
from src.api import dependencies


def test_invalid_replay_logs_five_ws_without_record_contents(
    monkeypatch: pytest.MonkeyPatch, project_root: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = project_root / "examples" / "traces" / "readme-run.sanitized.jsonl"
    monkeypatch.setattr(dependencies, "REPLAY_TRACE", fixture)
    with TestClient(app) as client:
        assert client.get("/events").status_code == 503

    stderr = capsys.readouterr().err
    event = next(record for record in map(json.loads, stderr.splitlines()) if record["event"] == "replay.load.failed")
    assert event["service"] == "agent-lens-backend"
    assert event["component"] == "replay"
    assert event["outcome"] == "failed"
    assert event["error_code"] == "replay_invalid"
    assert event["timestamp"].endswith("Z")
    assert str(fixture) not in stderr
    assert "juniper" not in stderr


def test_replay_correlates_file_load_and_sse_delivery_without_content(
    monkeypatch: pytest.MonkeyPatch, capture_root: Path,
    telemetry_exports: tuple[InMemorySpanExporter, InMemoryLogRecordExporter],
) -> None:
    marker = "synthetic-content-should-stay-private"
    with TemporaryDirectory(prefix="test-otel-replay-", dir=capture_root) as directory:
        trace = Path(directory) / "synthetic.jsonl"
        trace.write_text(json.dumps({"elapsed_ns": 0, "event": {"type": "demo", "text": marker}}) + "\n")
        monkeypatch.setattr(dependencies, "REPLAY_TRACE", trace)
        with TestClient(app) as client:
            assert client.get("/openapi.json").status_code == 200
            response = client.get("/events", headers={
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            })
            assert response.status_code == 200
            assert marker in response.text
            assert "event: end" in response.text

    span_exporter, log_exporter = telemetry_exports
    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    assert set(spans) == {"GET /events", "replay.load", "replay.emit"}
    context = spans["GET /events"].context
    assert context is not None and spans["GET /events"].parent is None
    assert {span.context.trace_id for span in spans.values() if span.context} == {context.trace_id}
    assert (spans["replay.emit"].attributes or {})["agent_lens.outcome"] == "completed"
    logs = {item.log_record.body: item.log_record for item in log_exporter.get_finished_logs()}
    assert set(logs) == {"replay.load.completed", "replay.sse.completed"}
    assert all(log.trace_id == context.trace_id for log in logs.values())
    assert marker not in str(logs)
    assert str(trace) not in str(logs)
    assert marker not in str([span.attributes for span in spans.values()])
