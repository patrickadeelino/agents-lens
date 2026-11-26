"""Record how one execution went and build the message that ends it."""

from asyncio import LimitOverrunError

from opentelemetry.trace import Span

from src.executor.agents.base import AgentRejected
from src.shared.executor_protocol import RunError, RunExit
from src.shared.observability import (
    EventProgress,
    event_log,
    exit_outcome,
    finish_span,
)


class ExecutionReport:
    def __init__(self, span: Span) -> None:
        self._span = span
        self._progress = EventProgress("executor.subprocess.first_event_read", "subprocess")

    def started(self) -> None:
        event_log("executor.subprocess.started", "subprocess", "started")

    def event_read(self) -> None:
        self._progress.advance(self._span)

    def finished(self, exit_code: int | None) -> RunExit:
        outcome, code = exit_outcome(exit_code)
        finish_span(self._span, outcome, error_code=code)
        event_log(
            f"executor.subprocess.{outcome}", "subprocess", outcome, error_code=code,
            started_at=self._progress.started_at, exit_code=exit_code, event_count=self._progress.count,
        )
        return RunExit(exit_code=exit_code)

    def start_failed(self) -> RunExit:
        finish_span(self._span, "failed", error_code="subprocess_start_failed")
        event_log(
            "executor.subprocess.failed", "subprocess", "failed", error_code="subprocess_start_failed",
            started_at=self._progress.started_at, event_count=self._progress.count,
        )
        return RunExit(exit_code=None)

    def failed(self, error: Exception) -> RunExit | RunError:
        if isinstance(error, LimitOverrunError):
            finish_span(self._span, "failed", error_code="event_too_large")
            return RunError(error="event_too_large")
        if isinstance(error, AgentRejected):
            return self._broken("agent_request_rejected", exit_code=1)
        return self._broken("agent_disconnected", exit_code=None)

    def _broken(self, error_code: str, *, exit_code: int | None) -> RunExit:
        finish_span(self._span, "failed", error_code=error_code)
        event_log("executor.subprocess.failed", "subprocess", "failed", error_code=error_code)
        return RunExit(exit_code=exit_code)
