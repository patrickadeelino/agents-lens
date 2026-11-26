"""Encode trace records as Server-Sent Events."""

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from time import monotonic

from src.backend.application.history.run_history import HistoryEvent
from src.backend.application.history.run_subscription import (
    SubscriptionEnded,
    SubscriptionMessage,
)
from src.shared.streaming import ClosingStreamingResponse
from src.shared.trace_events import TimedEvent


def record_frame(record: TimedEvent, *, event_id: int | None = None) -> str:
    cursor = f"id: {event_id}\n" if event_id is not None else ""
    return f"{cursor}event: record\ndata: {record.model_dump_json()}\n\n"


def end_frame(payload: dict[str, int | str | None] | None = None) -> str:
    return f"event: end\ndata: {json.dumps(payload or {})}\n\n"


class RunSubscriptionResponse(ClosingStreamingResponse):
    """Encode a run subscription for the lifespan of one SSE connection."""

    _heartbeat_seconds = 15.0

    def __init__(self, messages: AsyncGenerator[SubscriptionMessage]) -> None:
        self._messages = messages
        super().__init__(self._encode_frames(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    async def _encode_frames(self) -> AsyncGenerator[str]:
        async with aclosing(self._messages) as messages:
            async for frame in self._due_frames(messages):
                yield frame

    async def _due_frames(self, messages: AsyncGenerator[SubscriptionMessage]) -> AsyncGenerator[str]:
        """Skip the pauses that arrive before the next keep-alive is due."""
        ping_at = monotonic() + self._heartbeat_seconds
        async for message in messages:
            if message is None and monotonic() < ping_at:
                continue
            yield self._encode(message)
            ping_at = monotonic() + self._heartbeat_seconds

    def _encode(self, message: SubscriptionMessage) -> str:
        if message is None:
            return ": keep-alive\n\n"
        if isinstance(message, HistoryEvent):
            return record_frame(message.record, event_id=message.event_id)
        return self._encode_end(message)

    @staticmethod
    def _encode_end(message: SubscriptionEnded) -> str:
        payload: dict[str, int | str | None] = {"exit_code": message.exit_code}
        if message.error is not None:
            payload["error"] = message.error
        return end_frame(payload)
