"""Continue reading an existing capture after a record cursor."""

from asyncio import sleep
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from typing import Literal, Protocol

from src.backend.application.conversation.run_continuation import RecordedResult
from src.backend.application.history.run_history import HistoryEvent, RunReadError
from src.shared.trace_events import TimedEvent


class RunTail(Protocol):
    async def read(self) -> str | RecordedResult | None: ...


class RunSubscriptionSource(Protocol):
    def open_tail(self) -> AbstractContextManager[RunTail]: ...


@dataclass(frozen=True)
class SubscriptionEnded:
    exit_code: int | None
    error: Literal["result_unconfirmed", "capture_unavailable"] | None = None


# None represents a temporary pause in the capture.
type SubscriptionMessage = HistoryEvent | SubscriptionEnded | None


class SubscribeToRun:
    """Follow a capture and select records after the subscriber's cursor."""

    def __init__(self, find_capture: Callable[[str], RunSubscriptionSource]) -> None:
        self._find_capture = find_capture

    def execute(self, run_id: str, *, after: int) -> AsyncGenerator[SubscriptionMessage]:
        try:
            capture = self._find_capture(run_id)
        except FileNotFoundError as error:
            raise RunReadError("run_not_found") from error
        except OSError as error:
            raise RunReadError("capture_unavailable") from error

        # Check availability before the HTTP adapter starts streaming.
        try:
            with capture.open_tail():
                pass
        except FileNotFoundError as error:
            raise RunReadError("trace_not_found") from error
        except OSError as error:
            raise RunReadError("capture_unavailable") from error
        return self._follow(capture, after)

    async def _follow(
        self, capture: RunSubscriptionSource, after: int,
    ) -> AsyncGenerator[SubscriptionMessage]:
        cursor = TailCursor(after)
        with suppress(OSError, ValueError), capture.open_tail() as tail:
            while not cursor.ended:
                yield await cursor.read(tail)
            return
        # Reached only when reading failed.
        yield SubscriptionEnded(None, "capture_unavailable")


class TailCursor:
    """Number the records of one tail and report those after the subscriber's position."""

    def __init__(self, after: int) -> None:
        self.ended = False
        self._after = after
        self._event_id = 0
        self._paused = False

    async def read(self, tail: RunTail) -> SubscriptionMessage:
        if self._paused:
            await sleep(0.1)
        message = await tail.read()
        while isinstance(message, str):
            self._event_id += 1
            if self._event_id > self._after:
                self._paused = False
                return HistoryEvent(self._event_id, TimedEvent.model_validate_json(message))
            message = await tail.read()
        self._paused = message is None
        if message is None:
            return None
        self.ended = True
        return SubscriptionEnded(message.exit_code, "result_unconfirmed" if message.exit_code is None else None)
