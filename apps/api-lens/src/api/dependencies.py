"""Build the services used by the HTTP routes."""

import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Annotated

from fastapi import HTTPException
from fastapi import Path as PathParam

from src.application.capture import EventCapture
from src.application.capture_run import CaptureRun
from src.application.replay import InvalidReplayOffsets, TimedReplay
from src.application.run_history import GetRunHistory
from src.application.run_subscription import SubscribeToRun
from src.domain.trace_reader import read_timed_trace
from src.infrastructure.capture_files import CaptureFiles
from src.infrastructure.executor_client import ExecutorClient
from src.observability import bind_run_id, event_log, finish_span, operation_span

from .schemas import PromptRequest

PROJECT_DIR = Path(__file__).resolve().parents[4]
REPLAY_TRACE = PROJECT_DIR / "examples" / "traces" / "readme-run.timed.sanitized.jsonl"
CAPTURE_ROOT = PROJECT_DIR / ".captures"
RunIdPath = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def get_run_history() -> GetRunHistory:
    return GetRunHistory(partial(CaptureFiles.find, CAPTURE_ROOT))


def get_run_subscription() -> SubscribeToRun:
    return SubscribeToRun(partial(CaptureFiles.find, CAPTURE_ROOT))


def get_replay() -> TimedReplay:
    """Prepare the sanitized example before starting the HTTP response."""
    started_at = monotonic()
    with operation_span("replay.load") as span:
        try:
            with REPLAY_TRACE.open(encoding="utf-8") as trace:
                records = tuple(read_timed_trace(trace))
            replay = TimedReplay(records)
        except (OSError, ValueError) as error:
            code = "replay_unavailable" if isinstance(error, OSError) else "replay_invalid"
            detail = (
                str(error) if isinstance(error, InvalidReplayOffsets)
                else "Replay trace is unavailable."
            )
            finish_span(span, "failed", error_code=code)
            event_log("replay.load.failed", "replay", "failed", error_code=code, started_at=started_at)
            raise HTTPException(status_code=503, detail=detail) from error
        finish_span(span, "completed")
        event_log("replay.load.completed", "replay", "completed", started_at=started_at)
        return replay


@dataclass(frozen=True)
class PromptCapture:
    run_id: str
    capture: CaptureRun


def get_prompt_capture(request: PromptRequest) -> PromptCapture:
    socket = os.environ.get("AGENT_LENS_EXECUTOR_SOCKET")
    if not socket:
        event_log("run.rejected", "api", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Host executor socket is not configured.")

    try:
        output = CaptureFiles.create(CAPTURE_ROOT, prefix="codex-prompt-")
    except OSError as error:
        event_log("run.rejected", "api", "rejected", error_code="capture_unavailable")
        raise HTTPException(status_code=503, detail="Capture directory is unavailable.") from error

    bind_run_id(output.directory.name)
    event_log("run.accepted", "api", "accepted")
    capture = CaptureRun(request.prompt, ExecutorClient(socket).run, EventCapture(), output)
    return PromptCapture(output.directory.name, capture)
