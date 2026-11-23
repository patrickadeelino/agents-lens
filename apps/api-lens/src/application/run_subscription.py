"""Continue reading an existing capture after a record cursor."""

from asyncio import sleep
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from src.domain.trace_events import TimedEvent

from .run_history import HistoryEvent, RecordedResult, RunReadError


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
        event_id = 0
        try:
            with capture.open_tail() as tail:
                while True:
                    message = await tail.read()
                    if message is None:
                        yield None
                        await sleep(0.1)
                        continue
                    if not isinstance(message, str):
                        yield SubscriptionEnded(
                            message.exit_code, "result_unconfirmed" if message.exit_code is None else None,
                        )
                        return
                    event_id += 1
                    if event_id > after:
                        yield HistoryEvent(event_id, TimedEvent.model_validate_json(message))
        except (OSError, ValueError):
            yield SubscriptionEnded(None, "capture_unavailable")
