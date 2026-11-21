"""Expose recorded trace replay over Server-Sent Events."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_replay
from src.api.sse import end_frame, record_frame
from src.application.replay import TimedReplay

router = APIRouter()


async def replay_frames(replay: TimedReplay) -> AsyncIterator[str]:
    async for record in replay.stream():
        yield record_frame(record)
    yield end_frame()


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(replay: Annotated[TimedReplay, Depends(get_replay)]) -> StreamingResponse:
    return StreamingResponse(
        replay_frames(replay),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
