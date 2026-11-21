"""Start an agent run and expose its capture over Server-Sent Events."""

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette.types import Send

from src.api.dependencies import get_prompt_capture
from src.api.sse import end_frame, record_frame
from src.application.capture_run import CaptureRun
from src.domain.trace_events import TimedEvent

router = APIRouter()


async def run_frames(capture: CaptureRun) -> AsyncGenerator[str]:
    async with aclosing(capture.stream()) as messages:
        async for message in messages:
            if isinstance(message, TimedEvent):
                yield record_frame(message)
            else:
                payload: dict[str, int | str | None] = {"exit_code": message.exit_code}
                if message.exit_code is None:
                    payload["error"] = message.error or "execution_failed"
                yield end_frame(payload)


class RunStreamingResponse(StreamingResponse):
    def __init__(self, capture: CaptureRun) -> None:
        self._frames = run_frames(capture)
        super().__init__(
            self._frames, media_type="text/event-stream", headers={"Cache-Control": "no-cache"},
        )

    async def stream_response(self, send: Send) -> None:
        # A disconnect can interrupt send while the iterator is suspended at yield.
        async with aclosing(self._frames):
            await super().stream_response(send)


@router.post(
    "/runs",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def prompt_run(capture: Annotated[CaptureRun, Depends(get_prompt_capture)]) -> StreamingResponse:
    return RunStreamingResponse(capture)
