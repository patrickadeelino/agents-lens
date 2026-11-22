"""Encode trace records as Server-Sent Events."""

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing

from fastapi.responses import StreamingResponse
from starlette.types import Send

from src.domain.trace_events import TimedEvent


def record_frame(record: TimedEvent) -> str:
    return f"event: record\ndata: {record.model_dump_json()}\n\n"


def end_frame(payload: dict[str, int | str | None] | None = None) -> str:
    return f"event: end\ndata: {json.dumps(payload or {})}\n\n"


class ClosingStreamingResponse(StreamingResponse):
    def __init__(
        self, frames: AsyncGenerator[str], *, media_type: str, headers: dict[str, str] | None = None,
    ) -> None:
        self._frames = frames
        super().__init__(frames, media_type=media_type, headers=headers)

    async def stream_response(self, send: Send) -> None:
        # A disconnect can interrupt send while the iterator is suspended at yield.
        async with aclosing(self._frames):
            await super().stream_response(send)
