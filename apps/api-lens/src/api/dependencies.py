"""Build the services used by the HTTP routes."""

import os
from pathlib import Path

from fastapi import HTTPException

from src.application.capture import EventCapture
from src.application.capture_run import CaptureRun
from src.application.replay import TimedReplay
from src.domain.trace_reader import read_timed_trace
from src.infrastructure.capture_files import CaptureFiles
from src.infrastructure.executor_client import ExecutorClient

from .schemas import PromptRequest

PROJECT_DIR = Path(__file__).resolve().parents[4]
REPLAY_TRACE = PROJECT_DIR / "examples" / "traces" / "readme-run.timed.sanitized.jsonl"
CAPTURE_ROOT = PROJECT_DIR / ".captures"


def get_replay() -> TimedReplay:
    """Prepare the sanitized example before starting the HTTP response."""
    try:
        with REPLAY_TRACE.open(encoding="utf-8") as trace:
            records = tuple(read_timed_trace(trace))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Replay trace is unavailable.") from error

    try:
        return TimedReplay(records)
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def get_prompt_capture(request: PromptRequest) -> CaptureRun:
    socket = os.environ.get("AGENT_LENS_EXECUTOR_SOCKET")
    if not socket:
        raise HTTPException(status_code=503, detail="Host executor socket is not configured.")

    try:
        output = CaptureFiles.create(CAPTURE_ROOT, prefix="codex-prompt-")
    except OSError as error:
        raise HTTPException(status_code=503, detail="Capture directory is unavailable.") from error

    return CaptureRun(request.prompt, ExecutorClient(socket).run, EventCapture(), output)
