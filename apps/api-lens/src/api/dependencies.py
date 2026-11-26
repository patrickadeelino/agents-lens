"""Build the services used by the HTTP routes."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import HTTPException
from fastapi import Path as PathParam

from src.application.capture import EventCapture
from src.application.capture_run import CaptureRun
from src.application.conversations import Conversations
from src.application.run_continuation import (
    RunSession,
    conversation_thread,
    describe_continuation,
)
from src.application.run_history import GetRunHistory
from src.application.run_subscription import SubscribeToRun
from src.infrastructure.capture_files import CaptureFiles
from src.infrastructure.conversation_store import ConversationStore
from src.infrastructure.executor_client import ExecutorClient
from src.observability import bind_run_id, event_log

from .schemas import NewPromptRequest, PromptRequest

PROJECT_DIR = Path(__file__).resolve().parents[4]
CAPTURE_ROOT = PROJECT_DIR / ".captures"
RunIdPath = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def get_run_history() -> GetRunHistory:
    return GetRunHistory(partial(CaptureFiles.find, CAPTURE_ROOT))


def get_run_subscription() -> SubscribeToRun:
    return SubscribeToRun(partial(CaptureFiles.find, CAPTURE_ROOT))


@dataclass(frozen=True)
class PromptCapture:
    run_id: str
    capture: CaptureRun


def reject_while_running() -> None:
    """Refuse a prompt before saving it while the single executor is running another one."""
    # Not atomic: two simultaneous prompts can pass, and the executor still rejects the second.
    if CaptureFiles.any_active(CAPTURE_ROOT):
        event_log("run.rejected", "api", "rejected", error_code="executor_busy")
        raise HTTPException(status_code=409, detail="executor_busy")


def get_prompt_capture(request: NewPromptRequest) -> PromptCapture:
    reject_while_running()
    socket = executor_socket()
    if request.workspace_id is not None:
        workspace = selected_workspace(socket, request.workspace_id)
    elif request.workspace_run_id is not None:
        workspace = saved_workspace(request.workspace_run_id)
        check_resume_workspace(socket, workspace)
    else:
        raise HTTPException(status_code=422, detail="Select a folder to start the conversation.")
    output = create_capture(request.prompt, workspace=workspace)
    return prepare_capture(request.prompt, socket, output, workspace=workspace)


def saved_workspace(run_id: str) -> str:
    try:
        session = CaptureFiles.find(CAPTURE_ROOT, run_id).read_session()
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Conversation was not found.") from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="History is unavailable.") from error
    if session is None:
        raise HTTPException(status_code=409, detail="Select the folder to start a new conversation.")
    return session.workspace


def selected_workspace(socket: str, selection_id: UUID) -> str:
    try:
        return ExecutorClient(socket).workspace(selection_id)
    except httpx.HTTPStatusError as error:
        raise HTTPException(status_code=409, detail="Select the folder again.") from error
    except (httpx.RequestError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Executor unavailable.") from error


def get_continuation_capture(run_id: RunIdPath, request: PromptRequest) -> PromptCapture:
    reject_while_running()
    origin, thread_id = resumable_capture(run_id)
    socket = executor_socket()
    workspace = saved_workspace(run_id)
    check_resume_workspace(socket, workspace)
    output = create_capture(request.prompt, previous_run_id=run_id, workspace=workspace)
    try:
        origin.reserve_successor(output)
    except FileExistsError as error:
        output.discard_unpublished()
        raise HTTPException(status_code=409, detail="Open the latest run to continue this conversation.") from error
    except OSError as error:
        output.discard_unpublished()
        raise HTTPException(status_code=503, detail="Capture directory is unavailable.") from error
    return prepare_capture(request.prompt, socket, output, thread_id=thread_id, workspace=workspace)


def check_resume_workspace(socket: str, workspace: str) -> None:
    try:
        ExecutorClient(socket).check_workspace(workspace)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 409:
            raise HTTPException(
                status_code=422, detail="This conversation's folder is unavailable or outside the workspace root. Restore the folder and try again.",
            ) from error
        raise HTTPException(status_code=503, detail="Executor unavailable.") from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=503, detail="Executor unavailable.") from error


def resumable_capture(run_id: str) -> tuple[CaptureFiles, UUID]:
    try:
        origin = CaptureFiles.find(CAPTURE_ROOT, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run was not found.") from error
    except OSError as error:
        raise HTTPException(status_code=503, detail="Run capture is unavailable.") from error
    try:
        result = origin.read_result()
        projection = Conversations(ConversationStore(CAPTURE_ROOT))
        projection.by_run(run_id)
        continuation = describe_continuation(run_id, origin, result)
        thread_id = conversation_thread(origin) if continuation.state == "available" else None
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Run capture is unavailable.") from error
    if thread_id is None:
        raise HTTPException(status_code=409, detail=continuation.reason or "This run cannot be continued.")
    return origin, thread_id


def executor_socket() -> str:
    socket = os.environ.get("AGENT_LENS_EXECUTOR_SOCKET")
    if not socket:
        event_log("run.rejected", "api", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Executor socket is not configured.")
    return socket


def create_capture(
    prompt: str, *, workspace: str, previous_run_id: str | None = None,
) -> CaptureFiles:
    try:
        output = CaptureFiles.create(CAPTURE_ROOT, prefix="codex-prompt-")
        output.write_session(RunSession(
            previous_run_id=previous_run_id, prompt=prompt,
            submitted_at=datetime.now(UTC), workspace=workspace,
        ))
    except OSError as error:
        event_log("run.rejected", "api", "rejected", error_code="capture_unavailable")
        raise HTTPException(status_code=503, detail="Capture directory is unavailable.") from error
    return output


def prepare_capture(
    prompt: str, socket: str, output: CaptureFiles, *, workspace: str, thread_id: UUID | None = None,
) -> PromptCapture:
    bind_run_id(output.directory.name)
    event_log("run.accepted", "api", "accepted")
    execute = partial(ExecutorClient(socket).run, thread_id=thread_id, workspace=workspace)
    capture = CaptureRun(prompt, execute, EventCapture(), output, expected_thread=thread_id)
    return PromptCapture(output.directory.name, capture)
