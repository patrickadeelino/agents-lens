from asyncio import run
from collections.abc import AsyncGenerator

import pytest
from starlette.types import Message

from src.api.routers import runs
from src.api.routers.runs import RunStreamingResponse
from src.application.capture_run import CaptureResult, CaptureRun, FlushedRecord
from src.domain.trace_events import TimedEvent, TraceEvent
from src.observability import SseRecordDetails


def test_run_response_closes_capture_stream_when_send_fails() -> None:
    detached = False

    class Capture(CaptureRun):
        def __init__(self) -> None:
            pass

        async def stream(self) -> AsyncGenerator[FlushedRecord | CaptureResult]:
            nonlocal detached
            try:
                yield FlushedRecord(TimedEvent(elapsed_ns=0, event=TraceEvent(type="turn.started")), 100_000)
            finally:
                detached = True

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise ConnectionResetError

    with pytest.raises(ConnectionResetError):
        run(RunStreamingResponse(Capture()).stream_response(send))

    assert detached


def test_run_measures_flush_to_sse_after_asgi_send(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ns = 2_000_000
    logged: list[SseRecordDetails] = []

    class Capture(CaptureRun):
        def __init__(self) -> None:
            pass

        async def stream(self) -> AsyncGenerator[FlushedRecord | CaptureResult]:
            yield FlushedRecord(TimedEvent(elapsed_ns=0, event=TraceEvent(type="turn.started")), 1_000_000)

    async def send(message: Message) -> None:
        nonlocal now_ns
        if message["type"] == "http.response.body" and message.get("body"):
            assert logged == []
            now_ns += 400_000

    def log(event: str, *args: object, record_details: SseRecordDetails, **kwargs: object) -> None:
        assert event == "run.sse.record_sent"
        logged.append(record_details)

    monkeypatch.setattr(runs, "monotonic_ns", lambda: now_ns)
    monkeypatch.setattr(runs, "event_log", log)
    run(RunStreamingResponse(Capture()).stream_response(send))

    assert [details.flush_to_sse_us for details in logged] == [1_400]
