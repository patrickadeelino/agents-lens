"""Build the services used by the HTTP routes."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from fastapi import Path as PathParam

from src.backend.api.schemas import NewPromptRequest, PromptRequest
from src.backend.application.capture.capture import EventCapture
from src.backend.application.capture.capture_run import CaptureRun, ExecutePrompt
from src.backend.application.conversation.continue_conversation import (
    ContinueConversation,
)
from src.backend.application.conversation.prompt_rules import PromptRefused, Refusal
from src.backend.application.conversation.start_conversation import (
    FolderChoice,
    FolderOfRun,
    SelectedFolder,
    StartConversation,
)
from src.backend.application.history.run_history import GetRunHistory
from src.backend.application.history.run_subscription import SubscribeToRun
from src.backend.infrastructure.executor_client import ExecutorClient
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.infrastructure.storage.conversation_store import ConversationStore
from src.shared.observability import bind_run_id, event_log

PROJECT_DIR = Path(__file__).resolve().parents[5]
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


REFUSALS: dict[Refusal, tuple[int, str]] = {
    "executor_busy": (409, "executor_busy"),
    "run_not_found": (404, "Run was not found."),
    "run_unreadable": (503, "Run capture is unavailable."),
    "not_continuable": (409, "This run cannot be continued."),
    "already_continued": (409, "Open the latest run to continue this conversation."),
    "conversation_not_found": (404, "Conversation was not found."),
    "history_unavailable": (503, "History is unavailable."),
    "folder_missing": (409, "Select the folder to start a new conversation."),
    "workspace_rejected": (
        422, "This conversation's folder is unavailable or outside the workspace root. Restore the folder and try again.",
    ),
    "folder_required": (422, "Select a folder to start the conversation."),
    "selection_expired": (409, "Select the folder again."),
    "executor_unavailable": (503, "Executor unavailable."),
    "capture_unavailable": (503, "Capture directory is unavailable."),
    "agent_unavailable": (409, "agent_unavailable"),
}


@contextmanager
def refusals_as_http() -> Iterator[None]:
    try:
        yield
    except PromptRefused as error:
        status, detail = REFUSALS[error.refusal]
        raise HTTPException(status_code=status, detail=error.reason or detail) from error


def conversation_store() -> ConversationStore:
    return ConversationStore(CAPTURE_ROOT)


def executor_client() -> ExecutorClient:
    return ExecutorClient(executor_socket())


def requested_folder(request: NewPromptRequest) -> FolderChoice | None:
    if request.workspace_id is not None:
        return SelectedFolder(request.workspace_id)
    if request.workspace_run_id is not None:
        return FolderOfRun(request.workspace_run_id)
    return None


def get_prompt_capture(request: NewPromptRequest) -> PromptCapture:
    use_case = StartConversation(conversation_store(), executor_client)
    with refusals_as_http():
        started = use_case.execute(request.prompt, requested_folder(request), request.agent)
    execute = partial(executor_client().run, workspace=started.workspace, agent=request.agent)
    return prepare_capture(request.prompt, started.run, execute)


def get_continuation_capture(run_id: RunIdPath, request: PromptRequest) -> PromptCapture:
    use_case = ContinueConversation(conversation_store(), executor_client)
    with refusals_as_http():
        accepted = use_case.execute(run_id, request.prompt)
    execute = partial(
        executor_client().run, workspace=accepted.workspace, agent=accepted.agent, thread_id=accepted.thread_id,
    )
    return prepare_capture(request.prompt, accepted.run, execute, expected_thread=accepted.thread_id)


def executor_socket() -> str:
    socket = os.environ.get("AGENT_LENS_EXECUTOR_SOCKET")
    if not socket:
        event_log("run.rejected", "api", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Executor socket is not configured.")
    return socket


def prepare_capture(
    prompt: str, output: CaptureFiles, execute: ExecutePrompt, *, expected_thread: UUID | None = None,
) -> PromptCapture:
    bind_run_id(output.directory.name)
    event_log("run.accepted", "api", "accepted")
    capture = CaptureRun(prompt, execute, EventCapture(), output, expected_thread=expected_thread)
    return PromptCapture(output.directory.name, capture)
