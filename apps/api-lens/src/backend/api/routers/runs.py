"""Start an agent run and expose its capture over Server-Sent Events."""

import os
from asyncio import CancelledError
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from time import monotonic_ns
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from opentelemetry.trace import INVALID_SPAN, Span, use_span
from starlette.requests import ClientDisconnect
from starlette.types import Send

from src.backend.api.dependencies import (
    PromptCapture,
    executor_socket,
    get_continuation_capture,
    get_prompt_capture,
)
from src.backend.api.sse import end_frame, record_frame
from src.backend.application.capture.capture_run import (
    CaptureResult,
    CaptureRun,
    FlushedRecord,
)
from src.backend.infrastructure.executor_client import ExecutorClient
from src.shared.observability import (
    EventProgress,
    Outcome,
    SseRecordDetails,
    event_log,
    finish_span,
    operation_span,
)

router = APIRouter()


@dataclass
class RunSseDelivery:
    span: Span = INVALID_SPAN
    progress: EventProgress = field(default_factory=lambda: EventProgress("run.sse.first_record_sent", "http"))
    max_flush_to_sse_us: int = 0

    def record_sent(self, message: FlushedRecord) -> None:
        flush_to_sse_us = (monotonic_ns() - message.flushed_at_ns) // 1_000
        self.max_flush_to_sse_us = max(self.max_flush_to_sse_us, flush_to_sse_us)
        with use_span(self.span, record_exception=False, set_status_on_exception=False):
            self.progress.advance(self.span)
            event_log(
                "run.sse.record_sent", "http", "completed",
                started_at=self.progress.started_at, event_count=self.progress.count,
                record_details=SseRecordDetails.from_event(message.record.event, flush_to_sse_us),
            )

    def end_sent(self, result: CaptureResult) -> None:
        self.span.add_event("run.sse.end_sent")
        with use_span(self.span, record_exception=False, set_status_on_exception=False):
            event_log(
                "run.sse.end_sent", "http", "completed", started_at=self.progress.started_at,
                event_count=self.progress.count, exit_code=result.exit_code,
            )

    def finish(self, outcome: Outcome, error_code: str | None) -> None:
        self.span.set_attribute("agent_lens.sse.records_sent", self.progress.count)
        if self.progress.count:
            self.span.set_attribute("agent_lens.sse.max_flush_to_sse_us", self.max_flush_to_sse_us)
        finish_span(self.span, outcome, error_code=error_code)


async def run_frames(
    messages: AsyncGenerator[FlushedRecord | CaptureResult], delivery: RunSseDelivery,
) -> AsyncGenerator[str]:
    async for message in messages:
        if isinstance(message, FlushedRecord):
            yield record_frame(message.record)
            # The response iterator resumes after ASGI send returns.
            delivery.record_sent(message)
        else:
            payload: dict[str, int | str | None] = {"exit_code": message.exit_code}
            if message.exit_code is None:
                payload["error"] = message.error or "execution_failed"
            yield end_frame(payload)
            delivery.end_sent(message)


class RunStreamingResponse(StreamingResponse):
    def __init__(self, capture: CaptureRun) -> None:
        self._delivery = RunSseDelivery()
        self._capture = capture
        super().__init__(
            iter(()), media_type="text/event-stream", headers={"Cache-Control": "no-cache"},
        )

    async def stream_response(self, send: Send) -> None:
        # The capture worker inherits POST /runs, independently of the SSE span.
        with operation_span("run.sse", activate=False) as span:
            self._delivery.span = span
            outcome: Outcome = "failed"
            error_code: str | None = "sse_delivery_failed"
            try:
                async with self._capture.subscribe() as messages, aclosing(run_frames(messages, self._delivery)) as frames:
                    self.body_iterator = frames
                    await super().stream_response(send)
            except (CancelledError, ClientDisconnect, OSError):
                outcome, error_code = "disconnected", "client_disconnected"
                raise
            else:
                outcome, error_code = "completed", None
            finally:
                self._delivery.finish(outcome, error_code)


RUN_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {
        "content": {"text/event-stream": {}},
        "headers": {"X-Run-ID": {"description": "ID of the saved capture", "schema": {"type": "string"}}},
    },
}


def accepted_run(run: PromptCapture) -> StreamingResponse:
    response = RunStreamingResponse(run.capture)
    response.headers["X-Run-ID"] = run.run_id
    return response


@router.get("/ready", status_code=204)
def readiness() -> Response:
    headers = {"Cache-Control": "no-store"}
    if os.environ.get("AGENT_LENS_DEMO") == "1":
        # The demo only reads recordings; the header tells the interface that no executor is expected.
        return Response(status_code=204, headers=headers | {"X-Agent-Lens-Mode": "demo"})
    try:
        ExecutorClient(executor_socket()).check_ready()
    except (HTTPException, httpx.HTTPError) as error:
        raise HTTPException(status_code=503, detail="Executor unavailable. Check the executor container.") from error
    return Response(status_code=204, headers=headers)


@router.post("/runs", response_class=StreamingResponse, responses=RUN_RESPONSE)
def prompt_run(run: Annotated[PromptCapture, Depends(get_prompt_capture)]) -> StreamingResponse:
    return accepted_run(run)


@router.post("/runs/{run_id}/continue", response_class=StreamingResponse, responses=RUN_RESPONSE)
def continue_run(run: Annotated[PromptCapture, Depends(get_continuation_capture)]) -> StreamingResponse:
    return accepted_run(run)
