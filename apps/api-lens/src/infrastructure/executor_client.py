"""Read the executor response through its Unix socket."""

from collections.abc import Iterator
from uuid import UUID

import httpx
from opentelemetry.trace import Span, SpanKind
from pydantic import TypeAdapter

from src.application.capture_run import (
    EventTooLarge,
    ExecutionResult,
    ExecutorRequestFailed,
    StdoutConsumer,
)
from src.observability import (
    EventProgress,
    event_log,
    exit_outcome,
    finish_span,
    operation_span,
    outgoing_context,
)

from .executor_protocol import (
    SESSION_PERSISTENCE_HEADER,
    ExecutorRequest,
    RunError,
    RunExit,
    RunOutput,
)

_EXECUTOR_MESSAGE = TypeAdapter(RunOutput | RunExit | RunError)


class ExecutorClient:
    def __init__(self, socket: str) -> None:
        self._socket = socket

    def run(self, prompt: str, on_stdout: StdoutConsumer, *, thread_id: UUID | None = None) -> ExecutionResult:
        """Deliver the stdout lines and return the terminal status."""
        with operation_span("executor.request", kind=SpanKind.CLIENT) as span:
            event_log("executor.request.started", "executor_client", "started")
            try:
                result = self._request(ExecutorRequest(prompt=prompt, thread_id=thread_id), on_stdout, span)
            except httpx.HTTPError as error:
                failure_code = executor_http_error_code(span, error)
                finish_span(span, "failed", error_code=failure_code)
                raise ExecutorRequestFailed(failure_code) from error
            except EventTooLarge:
                finish_span(span, "failed", error_code="event_too_large")
                raise
            except (OSError, ValueError):
                finish_span(span, "failed", error_code="capture_stream_invalid")
                raise
            outcome, code = exit_outcome(result.exit_code)
            finish_span(span, outcome, error_code=code)
            return result

    def _request(self, request: ExecutorRequest, on_stdout: StdoutConsumer, span: Span) -> ExecutionResult:
        progress = EventProgress("executor.request.first_event_received", "executor_client")
        transport = httpx.HTTPTransport(uds=self._socket)
        timeout = httpx.Timeout(120, connect=5.0)
        with (
            httpx.Client(
                base_url="http://executor-host", timeout=timeout, trust_env=False, transport=transport,
            ) as client,
            client.stream(
                "POST", "/run", json=request.model_dump(mode="json", exclude_none=True), headers=outgoing_context(),
            ) as response,
        ):
            exit_code = _consume_response(response, on_stdout, progress, span)
            return ExecutionResult(exit_code, response.headers.get(SESSION_PERSISTENCE_HEADER) == "enabled")


def executor_http_error_code(span: Span, error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        span.set_attribute("http.response.status_code", error.response.status_code)
        return "executor_http_error"
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "executor_unreachable"
    return "executor_transport_error"


def _consume_response(
    response: httpx.Response, on_stdout: StdoutConsumer, progress: EventProgress, span: Span,
) -> int | None:
    terminal: RunExit | RunError | None = None

    def stdout() -> Iterator[str]:
        nonlocal terminal
        response.raise_for_status()
        for line in response.iter_lines():
            if terminal is not None:
                raise ValueError("Executor sent data after its exit status")
            message = _EXECUTOR_MESSAGE.validate_json(line)
            if isinstance(message, (RunExit, RunError)):
                terminal = message
                span.add_event("executor.request.terminal_received")
            else:
                progress.advance(span)
                yield message.stdout

    on_stdout(stdout())
    return _validated_exit_code(terminal)


def _validated_exit_code(terminal: RunExit | RunError | None) -> int | None:
    if terminal is None:
        raise ValueError("Executor stream ended without an exit status")
    if isinstance(terminal, RunError):
        raise EventTooLarge
    return terminal.exit_code
