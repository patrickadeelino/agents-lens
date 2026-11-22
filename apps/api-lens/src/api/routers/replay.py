"""Expose recorded trace replay over Server-Sent Events."""

from asyncio import CancelledError
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette.types import Send

from src.api.dependencies import get_replay
from src.api.sse import ClosingStreamingResponse, end_frame, record_frame
from src.application.replay import TimedReplay
from src.observability import finish_span, operation_span

router = APIRouter()


async def replay_frames(replay: TimedReplay) -> AsyncGenerator[str]:
    async for record in replay.stream():
        yield record_frame(record)
    yield end_frame()


class ReplayStreamingResponse(ClosingStreamingResponse):
    async def stream_response(self, send: Send) -> None:
        with operation_span("replay.emit") as span:
            try:
                await super().stream_response(send)
            except (CancelledError, OSError):
                finish_span(span, "disconnected", error_code="client_disconnected")
                raise
            else:
                finish_span(span, "completed")


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(replay: Annotated[TimedReplay, Depends(get_replay)]) -> StreamingResponse:
    return ReplayStreamingResponse(
        replay_frames(replay),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
