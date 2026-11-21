"""Capture a live run and publish each record after it is flushed."""

import asyncio
import logging
from asyncio import Queue
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, TextIO

from src.domain.trace_events import TimedEvent

from .capture import EventCapture, RecordCallback

logger = logging.getLogger(__name__)

type StdoutConsumer = Callable[[Iterable[str]], None]
type ExecutePrompt = Callable[[str, StdoutConsumer], int | None]


class CaptureOutput(Protocol):
    def open_trace(self) -> TextIO: ...

    def write_result(self, exit_code: int | None) -> None: ...


class EventTooLarge(ValueError):
    """The executor ended capture at an oversized JSONL event."""


@dataclass(frozen=True)
class CaptureResult:
    exit_code: int | None
    error: Literal["event_too_large"] | None = None


class CaptureRun:
    def __init__(
        self, prompt: str, execute: ExecutePrompt, capture: EventCapture, output: CaptureOutput,
    ) -> None:
        self._prompt = prompt
        self._execute = execute
        self._capture = capture
        self._output = output

    def _record(self, publish: RecordCallback) -> int | None:
        def record_stdout(lines: Iterable[str]) -> None:
            with self._output.open_trace() as trace:
                self._capture.write(lines, trace, on_record=publish)

        exit_code = self._execute(self._prompt, record_stdout)
        self._output.write_result(exit_code)
        return exit_code

    def _capture_result(self, publish: Callable[[TimedEvent | None], None]) -> CaptureResult:
        try:
            return CaptureResult(exit_code=self._record(publish))
        except EventTooLarge:
            return CaptureResult(exit_code=None, error="event_too_large")
        except (OSError, ValueError) as error:
            logger.warning("Run capture failed (%s)", type(error).__name__)
            return CaptureResult(exit_code=None)
        finally:
            publish(None)

    async def stream(self) -> AsyncGenerator[TimedEvent | CaptureResult]:
        """Deliver flushed records while an SSE subscriber is attached."""
        loop = asyncio.get_running_loop()
        records: Queue[TimedEvent | None] = Queue()
        subscriber: Queue[TimedEvent | None] | None = records

        def enqueue(record: TimedEvent | None) -> None:
            # Closing the stream may race with a callback already scheduled by the worker.
            if subscriber is not None:
                subscriber.put_nowait(record)

        def publish(record: TimedEvent | None) -> None:
            if subscriber is not None:
                loop.call_soon_threadsafe(enqueue, record)

        execution = loop.run_in_executor(None, self._capture_result, publish)
        try:
            while (record := await records.get()) is not None:
                yield record
            yield await execution
        finally:
            subscriber = None
