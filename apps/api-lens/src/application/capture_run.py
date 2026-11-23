"""Capture a live run and publish each record after it is flushed."""

import asyncio
from asyncio import Queue
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import AbstractContextManager
from contextvars import copy_context
from dataclasses import dataclass
from time import monotonic_ns
from typing import Literal, Protocol, TextIO

from src.domain.trace_events import TimedEvent
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
type ExecutePrompt = Callable[[str, StdoutConsumer], int | None]


class CaptureOutput(Protocol):
    def open_trace(self) -> TextIO: ...

    def track_activity(self) -> AbstractContextManager[None]: ...

    def write_result(self, exit_code: int | None) -> None: ...


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
    ) -> None:
        self._prompt = prompt
        self._execute = execute
        self._capture = capture
        self._output = output

    def _record(self, publish: FlushedRecordCallback, progress: EventProgress) -> int | None:
        def record_stdout(lines: Iterable[str]) -> None:
            commands = ObservedCommandSpans()

            def saved(record: TimedEvent) -> None:
                flushed = FlushedRecord(record, monotonic_ns())
                progress.advance()
                publish(flushed)
                commands.observe(record)

            try:
                with self._output.open_trace() as trace:
                    self._capture.write(lines, trace, on_start=commands.start, on_record=saved)
            finally:
                commands.finish()

        exit_code = self._execute(self._prompt, record_stdout)
        self._write_result(exit_code)
        return exit_code

    def _write_result(self, exit_code: int | None) -> None:
        with operation_span("capture.write_result") as span:
            try:
                self._output.write_result(exit_code)
            except OSError:
                finish_span(span, "failed", error_code="capture_result_write_failed")
                raise
            finish_span(span, "completed")

    def _capture_result(self, publish: Callable[[FlushedRecord | None], None]) -> CaptureResult:
        try:
            with self._output.track_activity(), operation_span("run.capture") as span:
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
            return CaptureResult(exit_code=None, error="event_too_large"), "failed", "event_too_large"
        except ExecutorRequestFailed as error:
            return CaptureResult(exit_code=None), "failed", error.error_code
        except OSError:
            return CaptureResult(exit_code=None), "failed", "capture_io_failed"
        except ValueError:
            return CaptureResult(exit_code=None), "failed", "capture_stream_invalid"
        outcome, code = exit_outcome(exit_code)
        return CaptureResult(exit_code), outcome, code

    async def stream(self) -> AsyncGenerator[FlushedRecord | CaptureResult]:
        """Deliver flushed records while an SSE subscriber is attached."""
        loop = asyncio.get_running_loop()
        records: Queue[FlushedRecord | None] = Queue()
        subscriber: Queue[FlushedRecord | None] | None = records

        def enqueue(record: FlushedRecord | None) -> None:
            # Closing the stream may race with a callback already scheduled by the worker.
            if subscriber is not None:
                subscriber.put_nowait(record)

        def publish(record: FlushedRecord | None) -> None:
            if subscriber is not None:
                loop.call_soon_threadsafe(enqueue, record)

        execution = loop.run_in_executor(None, copy_context().run, self._capture_result, publish)
        try:
            while (record := await records.get()) is not None:
                yield record
            yield await execution
        finally:
            subscriber = None
