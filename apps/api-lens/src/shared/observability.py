"""Safe operational logs and spans for the Agent Lens services."""

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from re import fullmatch
from threading import Thread
from time import monotonic, time_ns
from typing import Literal, Self

from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from src.shared.trace_events import TimedEvent, TraceEvent

type Outcome = Literal["accepted", "started", "completed", "failed", "rejected", "disconnected", "unconfirmed"]
type Fields = dict[str, str | int]

_PROPAGATOR = TraceContextTextMapPropagator()
_NOOP_TRACER = trace.NoOpTracerProvider().get_tracer("agent-lens")
_RUN_HEADER = "x-agent-lens-run-id"


_ITEM_EVENT_TYPES = frozenset({"item.started", "item.updated", "item.completed"})
_CODEX_EVENT_TYPES = _ITEM_EVENT_TYPES | {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"}
_CODEX_ITEM_TYPES = frozenset({
    "agent_message", "collab_tool_call", "command_execution", "error", "file_change", "mcp_tool_call",
    "reasoning", "todo_list", "web_search",
})
_COMMAND_STATUSES = frozenset({"in_progress", "completed", "failed"})


def allowed_label(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def command_exit_code(value: object) -> int | None:
    return value if type(value) is int and -(2**31) <= value < 2**31 else None


@dataclass(frozen=True)
class SseRecordDetails:
    """Operational metadata selected from one Codex event."""

    event_type: str
    flush_to_sse_us: int
    item_type: str | None = None
    command_status: str | None = None
    command_exit_code: int | None = None

    @classmethod
    def from_event(cls, event: TraceEvent, flush_to_sse_us: int) -> Self:
        event_type = allowed_label(event.type, _CODEX_EVENT_TYPES) or "other"
        item = (event.model_extra or {}).get("item")
        if event_type not in _ITEM_EVENT_TYPES or not isinstance(item, dict):
            return cls(event_type, flush_to_sse_us)
        item_type = allowed_label(item.get("type"), _CODEX_ITEM_TYPES) or "other"
        if item_type != "command_execution":
            return cls(event_type, flush_to_sse_us, item_type)
        return cls(
            event_type, flush_to_sse_us, item_type,
            allowed_label(item.get("status"), _COMMAND_STATUSES), command_exit_code(item.get("exit_code")),
        )

    def fields(self) -> Fields:
        fields: Fields = {"event_type": self.event_type, "flush_to_sse_us": self.flush_to_sse_us}
        optional = (
            ("item_type", self.item_type), ("command_status", self.command_status),
            ("command_exit_code", self.command_exit_code),
        )
        fields.update({name: value for name, value in optional if value is not None})
        return fields


@dataclass
class Telemetry:
    service: str
    tracer_provider: TracerProvider | None = None
    logger_provider: LoggerProvider | None = None

    def configure(self) -> None:
        if os.environ.get("AGENT_LENS_OTEL_ENABLED") != "1":
            return
        try:
            self.configure_otlp(os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
        except (KeyError, ValueError):
            self.emit("telemetry.configuration_failed", "telemetry", "failed", error_code="otlp_configuration")

    def configure_otlp(self, endpoint: str) -> None:
        endpoint = endpoint.rstrip("/")
        if not fullmatch(r"https?://[^/?#@\s]+(?:/[^?#@\s]*)?", endpoint):
            raise ValueError("Invalid OTLP/HTTP endpoint")
        resource = Resource({"service.name": self.service})
        tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=1)),
        )
        logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=1)),
        )
        self.tracer_provider = tracer_provider
        self.logger_provider = logger_provider

    def shutdown(self) -> None:
        providers = (self.tracer_provider, self.logger_provider)
        deadline = monotonic() + 2
        threads = [Thread(target=close_provider, args=(provider,), daemon=True) for provider in providers if provider]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(max(0, deadline - monotonic()))
        self.tracer_provider = None
        self.logger_provider = None

    def emit(
        self, event: str, component: str, outcome: Outcome, *, error_code: str | None = None,
        duration_ms: int | None = None, exit_code: int | None = None, run_id: str | None = None,
        event_count: int | None = None, record_details: SseRecordDetails | None = None,
    ) -> None:
        """Accept only operational fields supplied by the application's call sites."""
        level = {
            "failed": SeverityNumber.ERROR, "rejected": SeverityNumber.WARN, "unconfirmed": SeverityNumber.WARN,
        }.get(outcome, SeverityNumber.INFO)
        payload: Fields = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "service": self.service, "component": component, "event": event, "outcome": outcome,
            "level": level.name,
        }
        payload.update(safe_details(error_code, duration_ms, exit_code, run_id, event_count))
        if record_details is not None:
            payload.update(record_details.fields())
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload.update(trace_id=f"{span_context.trace_id:032x}", span_id=f"{span_context.span_id:016x}")
        with suppress(OSError, ValueError):
            sys.stderr.write(json.dumps(payload, separators=(",", ":")) + "\n")
            sys.stderr.flush()
        if self.logger_provider is not None:
            with suppress(Exception):
                self.logger_provider.get_logger("agent-lens").emit(
                    body=event, event_name=event, attributes=payload, timestamp=time_ns(),
                    severity_number=level, severity_text=level.name,
                )


def safe_details(
    error_code: str | None, duration_ms: int | None, exit_code: int | None, run_id: str | None,
    event_count: int | None,
) -> Fields:
    details: Fields = {}
    if error_code is not None:
        details["error_code"] = error_code
    if duration_ms is not None:
        details["duration_ms"] = duration_ms
    if exit_code is not None:
        details["exit_code"] = exit_code
    if run_id is not None and valid_run_id(run_id):
        details["run_id"] = run_id
    if event_count is not None:
        details["event_count"] = event_count
    return details


def close_provider(provider: TracerProvider | LoggerProvider) -> None:
    with suppress(Exception):
        provider.shutdown()


@dataclass
class Operation:
    telemetry: Telemetry
    run_id: str | None = None


_operation: ContextVar[Operation | None] = ContextVar("agent_lens_operation", default=None)


@contextmanager
def observe_operation(telemetry: Telemetry, run_id: str | None = None) -> Iterator[None]:
    token = _operation.set(Operation(telemetry, run_id))
    try:
        yield
    finally:
        _operation.reset(token)


def valid_run_id(run_id: str) -> bool:
    return fullmatch(r"codex-prompt-[a-z0-9_]{8}", run_id) is not None


def bind_run_id(run_id: str) -> None:
    operation = _operation.get()
    if operation is not None and valid_run_id(run_id):
        operation.run_id = run_id
        trace.get_current_span().set_attribute("agent_lens.run_id", run_id)


def outgoing_context() -> dict[str, str]:
    headers: dict[str, str] = {}
    _PROPAGATOR.inject(headers)
    operation = _operation.get()
    if operation is not None and operation.run_id is not None:
        headers[_RUN_HEADER] = operation.run_id
    return headers


def incoming_context(headers: dict[str, str]) -> tuple[Context, str | None]:
    run_id = headers.get(_RUN_HEADER, "")
    return _PROPAGATOR.extract(headers, context=Context()), run_id if valid_run_id(run_id) else None


@contextmanager
def operation_span(
    name: str, *, kind: SpanKind = SpanKind.INTERNAL, context: Context | None = None, activate: bool = True,
    start_time_ns: int | None = None, end_time_ns: int | None = None,
) -> Iterator[Span]:
    operation = _operation.get()
    provider = operation.telemetry.tracer_provider if operation else None
    tracer = provider.get_tracer("agent-lens") if provider else _NOOP_TRACER
    span = tracer.start_span(
        name, kind=kind, context=context, start_time=start_time_ns,
        record_exception=False, set_status_on_exception=False,
    )
    try:
        if operation is not None and operation.run_id is not None:
            span.set_attribute("agent_lens.run_id", operation.run_id)
        scope = (
            trace.use_span(span, record_exception=False, set_status_on_exception=False)
            if activate else nullcontext()
        )
        with scope:
            yield span
    finally:
        span.end(end_time=end_time_ns)


def event_log(
    event: str, component: str, outcome: Outcome, *, error_code: str | None = None,
    started_at: float | None = None, exit_code: int | None = None, event_count: int | None = None,
    record_details: SseRecordDetails | None = None,
) -> None:
    operation = _operation.get()
    if operation is None:
        return
    elapsed = round((monotonic() - started_at) * 1000) if started_at is not None else None
    operation.telemetry.emit(
        event, component, outcome, error_code=error_code, duration_ms=elapsed,
        exit_code=exit_code, run_id=operation.run_id, event_count=event_count, record_details=record_details,
    )


@dataclass
class EventProgress:
    """Count events at one boundary and log the first observation."""

    first_event: str
    component: str
    started_at: float = field(default_factory=monotonic, init=False)
    count: int = field(default=0, init=False)

    def advance(self, span: Span | None = None) -> None:
        self.count += 1
        if self.count == 1:
            if span is not None:
                span.add_event(self.first_event)
            event_log(self.first_event, self.component, "completed", started_at=self.started_at)


def finish_span(span: Span, outcome: Outcome, *, error_code: str | None = None) -> None:
    span.set_attribute("agent_lens.outcome", outcome)
    if error_code is not None:
        span.set_attribute("agent_lens.error_code", error_code)
    if outcome in {"failed", "unconfirmed"}:
        span.set_status(StatusCode.ERROR)


class ObservedCommandSpans:
    """Emit detached spans for paired command events in one executor stream."""

    def __init__(self) -> None:
        self._parent = trace.get_current_span()
        self._origin_ns = 0
        self._starts: dict[str, int] = {}
        self._completed: set[str] = set()
        self._missing_start = 0

    def start(self) -> None:
        # Anchor capture's monotonic offsets on the wall clock used by OpenTelemetry.
        self._origin_ns = time_ns()

    def observe(self, record: TimedEvent) -> None:
        event_type = record.event.type
        if not self._parent.is_recording() or event_type not in {"item.started", "item.completed"}:
            return
        item = (record.event.model_extra or {}).get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            return
        item_id = item.get("id")
        if not isinstance(item_id, str) or fullmatch(r"(?:item_[0-9]{1,20}|exec-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", item_id) is None:
            return
        if item_id in self._completed:
            return
        if event_type == "item.started":
            self._starts.setdefault(item_id, record.elapsed_ns)
        else:
            self._complete(item_id, record.elapsed_ns, item.get("status"), item.get("exit_code"))

    def _complete(self, item_id: str, end_offset_ns: int, raw_status: object, raw_exit_code: object) -> None:
        self._completed.add(item_id)
        start_offset_ns = self._starts.pop(item_id, None)
        if start_offset_ns is None:
            self._missing_start += 1
            return
        status = allowed_label(raw_status, _COMMAND_STATUSES)
        exit_code = command_exit_code(raw_exit_code)
        with operation_span(
            "codex.command", activate=False,
            start_time_ns=self._origin_ns + start_offset_ns,
            end_time_ns=self._origin_ns + end_offset_ns,
        ) as span:
            span.set_attribute("agent_lens.item_id", item_id)
            if status is not None:
                span.set_attribute("agent_lens.command.status", status)
            if exit_code is not None:
                span.set_attribute("agent_lens.command.exit_code", exit_code)
            finish_command_span(span, status, exit_code)

    def finish(self) -> None:
        if self._missing_start:
            self._parent.set_attribute("agent_lens.command.missing_start_count", self._missing_start)
        if self._starts:
            self._parent.set_attribute("agent_lens.command.missing_completion_count", len(self._starts))


def finish_command_span(span: Span, status: str | None, exit_code: int | None) -> None:
    if status == "failed" or (exit_code is not None and exit_code != 0):
        finish_span(span, "failed", error_code="codex_command_failed")
    elif status == "completed" or exit_code == 0:
        finish_span(span, "completed")
    else:
        finish_span(span, "unconfirmed", error_code="codex_command_result_unconfirmed")


def exit_outcome(exit_code: int | None) -> tuple[Outcome, str | None]:
    if exit_code is None:
        return "unconfirmed", "executor_result_unconfirmed"
    if exit_code != 0:
        return "failed", "subprocess_failed"
    return "completed", None
