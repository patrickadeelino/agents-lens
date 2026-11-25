"""Capture a live run and publish each record after it is flushed."""

import asyncio
import re
from asyncio import Event, Future, Queue
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import AbstractContextManager, aclosing, asynccontextmanager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Literal, Protocol, TextIO
from uuid import UUID

from src.domain.trace_events import TimedEvent
from src.domain.turn_summary import TurnEvidence, TurnSummary
from src.observability import (
    EventProgress,
    ObservedCommandSpans,
    Outcome,
    event_log,
    exit_outcome,
    finish_span,
    operation_span,
)

from .capture import EventCapture

type StdoutConsumer = Callable[[Iterable[str]], None]


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int | None
    persistence_confirmed: bool


type ExecutePrompt = Callable[[str, StdoutConsumer], ExecutionResult]


class CaptureOutput(Protocol):
    def open_trace(self) -> TextIO: ...

    def track_activity(self) -> AbstractContextManager[None]: ...

    def write_result(
        self, exit_code: int | None, *, thread_id: UUID | None = None, persistence_confirmed: bool = False,
        assistant_markdown: str | None = None, last_turn: str | None = None, finished_at: datetime | None = None,
        conflicting_thread_id: UUID | None = None,
        capture_failed: bool = False, summary: TurnSummary | None = None,
    ) -> None: ...

    def ensure_trace(self) -> None: ...


class ExecutorRequestFailed(ConnectionError):
    """The host executor could not deliver its HTTP stream."""

    def __init__(self, error_code: str) -> None:
        super().__init__("Executor request failed.")
        self.error_code = error_code


class EventTooLarge(ValueError):
    """The executor ended capture at an oversized JSONL event."""


@dataclass(frozen=True)
class CaptureResult:
    exit_code: int | None
    error: Literal["event_too_large"] | None = None


@dataclass(frozen=True)
class FlushedRecord:
    record: TimedEvent
    flushed_at_ns: int


type FlushedRecordCallback = Callable[[FlushedRecord], None]


class CaptureRun:
    def __init__(
        self, prompt: str, execute: ExecutePrompt, capture: EventCapture, output: CaptureOutput,
        *, expected_thread: UUID | None = None,
    ) -> None:
        self._accepted_at_ns = monotonic_ns()
        self._evidence = TurnEvidence()
        self._prompt = prompt
        self._execute = execute
        self._capture = capture
        self._output = output
        self._expected_thread = expected_thread
        self._assistant_markdown: str | None = None
        self._last_turn: str | None = None
        self._conflicting_thread_id: UUID | None = None

    def _observe_summary(self, record: TimedEvent) -> None:
        event = record.event
        if event.type in {"turn.started", "turn.completed", "turn.failed"}:
            self._last_turn = event.type
        item = getattr(event, "item", None)
        if event.type == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip() and not re.fullmatch(r"\s*\[redacted:[^\]]+\]\s*", text, re.IGNORECASE):
                self._assistant_markdown = text

    def _record(self, publish: FlushedRecordCallback, progress: EventProgress) -> int | None:
        threads: set[UUID | None] = set()

        def record_stdout(lines: Iterable[str]) -> None:
            commands = ObservedCommandSpans()

            def saved(record: TimedEvent) -> None:
                flushed = FlushedRecord(record, monotonic_ns())
                progress.advance()
                self._observe_summary(record)
                self._evidence.observe(record)
                publish(flushed)
                commands.observe(record)
                if record.event.type == "thread.started":
                    threads.add(observed_thread(getattr(record.event, "thread_id", None)))

            try:
                with self._output.open_trace() as trace:
                    self._capture.write(lines, trace, on_start=commands.start, on_record=saved)
            finally:
                commands.finish()

        result = self._execute(self._prompt, record_stdout)
        thread_id = next(iter(threads)) if len(threads) == 1 else None
        if self._expected_thread is not None and thread_id != self._expected_thread:
            if thread_id is not None and result.persistence_confirmed:
                self._conflicting_thread_id = thread_id
            thread_id = None
        self._write_result(
            result.exit_code, thread_id=thread_id,
            persistence_confirmed=thread_id is not None and result.persistence_confirmed,
        )
        return result.exit_code

    def _write_result(
        self, exit_code: int | None, *, thread_id: UUID | None = None, persistence_confirmed: bool = False,
        capture_failed: bool = False,
    ) -> None:
        with operation_span("capture.write_result") as span:
            try:
                self._output.write_result(
                    exit_code, thread_id=thread_id, persistence_confirmed=persistence_confirmed,
                    assistant_markdown=self._assistant_markdown, last_turn=self._last_turn, finished_at=datetime.now(UTC),
                    conflicting_thread_id=self._conflicting_thread_id,
                    capture_failed=capture_failed,
                    summary=self._evidence.finish(
                        interrupted=capture_failed or exit_code is None,
                        total_duration_ns=monotonic_ns() - self._accepted_at_ns,
                    ),
                )
            except OSError:
                finish_span(span, "failed", error_code="capture_result_write_failed")
                raise
            finish_span(span, "completed")

    def _failed_capture(
        self, code: str, *, error: Literal["event_too_large"] | None = None,
    ) -> tuple[CaptureResult, Outcome, str]:
        try:
            self._output.ensure_trace()
            self._write_result(None, capture_failed=True)
        except OSError:
            code = "capture_io_failed"
        return CaptureResult(exit_code=None, error=error), "failed", code

    def _capture_result(
        self, publish: Callable[[FlushedRecord | None], None], notify_started: Callable[[], None],
    ) -> CaptureResult:
        try:
            with self._output.track_activity(), operation_span("run.capture") as span:
                notify_started()
                progress = EventProgress("run.capture.first_record_saved", "capture")
                result, outcome, code = self._capture_outcome(publish, progress)
                finish_span(span, outcome, error_code=code)
                event_log(
                    f"run.capture.{outcome}", "capture", outcome, error_code=code,
                    started_at=progress.started_at, exit_code=result.exit_code, event_count=progress.count,
                )
                return result
        finally:
            publish(None)

    def _capture_outcome(
        self, publish: FlushedRecordCallback, progress: EventProgress,
    ) -> tuple[CaptureResult, Outcome, str | None]:
        try:
            exit_code = self._record(publish, progress)
        except EventTooLarge:
            return self._failed_capture("event_too_large", error="event_too_large")
        except ExecutorRequestFailed as error:
            return self._failed_capture(error.error_code)
        except OSError:
            return self._failed_capture("capture_io_failed")
        except ValueError:
            return self._failed_capture("capture_stream_invalid")
        outcome, code = exit_outcome(exit_code)
        return CaptureResult(exit_code), outcome, code

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncGenerator[FlushedRecord | CaptureResult]]:
        """Deliver flushed records while an SSE subscriber is attached."""
        loop = asyncio.get_running_loop()
        records: Queue[FlushedRecord | None] = Queue()
        started = Event()
        subscriber: Queue[FlushedRecord | None] | None = records

        def enqueue(record: FlushedRecord | None) -> None:
            # Closing the stream may race with a callback already scheduled by the worker.
            if subscriber is not None:
                subscriber.put_nowait(record)

        def publish(record: FlushedRecord | None) -> None:
            if subscriber is not None:
                loop.call_soon_threadsafe(enqueue, record)

        def notify_started() -> None:
            loop.call_soon_threadsafe(started.set)

        execution = loop.run_in_executor(None, copy_context().run, self._capture_result, publish, notify_started)
        execution.add_done_callback(lambda _: started.set())

        try:
            # Ensure a refresh can discover the worker as soon as the POST returns its headers.
            await started.wait()
            async with aclosing(capture_messages(records, execution)) as messages:
                yield messages
        finally:
            subscriber = None


async def capture_messages(
    records: Queue[FlushedRecord | None], execution: Future[CaptureResult],
) -> AsyncGenerator[FlushedRecord | CaptureResult]:
    while (record := await records.get()) is not None:
        yield record
    yield await execution


def observed_thread(value: object) -> UUID | None:
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    return None
