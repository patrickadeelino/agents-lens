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
    FolderListing,
    RunError,
    RunExit,
    RunOutput,
    WorkspaceSelection,
)

_EXECUTOR_MESSAGE = TypeAdapter(RunOutput | RunExit | RunError)
READ_TIMEOUT_SECONDS = 120.0


class ExecutorClient:
    def __init__(self, socket: str) -> None:
        self._socket = socket

    def check_ready(self) -> None:
        self._workspace_request("GET", "/ready", timeout=2)

    def check_workspace(self, workspace: str) -> None:
        self._workspace_request("POST", "/workspace-check", timeout=2, payload={"workspace": workspace})

    def list_folders(self, path: str) -> FolderListing:
        response = self._workspace_request("GET", "/folders", timeout=5, params={"path": path})
        return FolderListing.model_validate_json(response.content)

    def select_folder(self, path: str) -> WorkspaceSelection:
        response = self._workspace_request("POST", "/folders", timeout=5, payload={"path": path})
        return WorkspaceSelection.model_validate_json(response.content)

    def workspace(self, selection_id: UUID) -> str:
        return WorkspaceSelection.model_validate_json(
            self._workspace_request("GET", f"/workspaces/{selection_id}", timeout=5).content,
        ).path

    def _workspace_request(
        self, method: str, path: str, *, timeout: int, payload: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=self._socket),
            base_url="http://executor-host", timeout=timeout, trust_env=False,
        ) as client:
            return client.request(method, path, json=payload, params=params).raise_for_status()

    def run(
        self, prompt: str, on_stdout: StdoutConsumer, *,
        workspace: str, thread_id: UUID | None = None,
    ) -> ExecutionResult:
        """Deliver the stdout lines and return the terminal status."""
        with operation_span("executor.request", kind=SpanKind.CLIENT) as span:
            event_log("executor.request.started", "executor_client", "started")
            try:
                result = self._request(ExecutorRequest(prompt=prompt, thread_id=thread_id, workspace=workspace), on_stdout, span)
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
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=5.0)
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
