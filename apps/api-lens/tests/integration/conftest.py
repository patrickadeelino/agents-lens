from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import app
from executor import app as executor_app
from src.api.dependencies import CAPTURE_ROOT, PROJECT_DIR
from src.observability import Telemetry


@pytest.fixture
def project_root() -> Path:
    return PROJECT_DIR


@pytest.fixture
def capture_root() -> Path:
    CAPTURE_ROOT.mkdir(mode=0o700, exist_ok=True)
    return CAPTURE_ROOT


@pytest.fixture
def telemetry_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[InMemorySpanExporter, InMemoryLogRecordExporter]]:
    spans = InMemorySpanExporter()
    logs = InMemoryLogRecordExporter()
    original = [(application.state, application.state.telemetry) for application in (app, executor_app)]
    monkeypatch.delenv("AGENT_LENS_OTEL_ENABLED", raising=False)
    for state, telemetry in original:
        service = telemetry.service
        resource = Resource({"service.name": service})
        tracer = TracerProvider(resource=resource, shutdown_on_exit=False)
        tracer.add_span_processor(SimpleSpanProcessor(spans))
        logger = LoggerProvider(resource=resource, shutdown_on_exit=False)
        logger.add_log_record_processor(SimpleLogRecordProcessor(logs))
        state.telemetry = Telemetry(service, tracer, logger)
    try:
        yield spans, logs
    finally:
        for state, telemetry in original:
            state.telemetry.shutdown()
            state.telemetry = telemetry
