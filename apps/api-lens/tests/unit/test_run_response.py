from asyncio import run
from collections.abc import AsyncGenerator

import pytest
from starlette.types import Message

from src.api.routers.runs import RunStreamingResponse
from src.application.capture_run import CaptureResult, CaptureRun
from src.domain.trace_events import TimedEvent, TraceEvent


def test_run_response_closes_capture_stream_when_send_fails() -> None:
    detached = False

    class Capture(CaptureRun):
        def __init__(self) -> None:
            pass

        async def stream(self) -> AsyncGenerator[TimedEvent | CaptureResult]:
            nonlocal detached
            try:
                yield TimedEvent(elapsed_ns=0, event=TraceEvent(type="turn.started"))
            finally:
                detached = True

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise ConnectionResetError

    with pytest.raises(ConnectionResetError):
        run(RunStreamingResponse(Capture()).stream_response(send))

    assert detached
