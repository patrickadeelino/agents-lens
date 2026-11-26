"""Expose saved conversation projections."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from src.backend.api import dependencies
from src.backend.api.dependencies import RunIdPath
from src.backend.application.conversation.conversations import (
    Conversations,
    ThreadDetail,
    ThreadList,
)
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.infrastructure.storage.conversation_store import ConversationStore

router = APIRouter()


def service() -> Conversations[CaptureFiles]:
    return Conversations(ConversationStore(dependencies.CAPTURE_ROOT))


def unavailable(error: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="Conversation storage is unavailable.")


@router.get("/threads")
def threads(response: Response) -> ThreadList:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service().list()
    except (OSError, ValueError) as error:
        raise unavailable(error) from error


@router.get("/threads/by-run/{run_id}")
def thread_by_run(run_id: RunIdPath, response: Response) -> ThreadDetail:
    response.headers["Cache-Control"] = "no-store"
    try:
        thread = service().by_run(run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Conversation was not found.") from error
    except (OSError, ValueError) as error:
        raise unavailable(error) from error
    if thread is None:
        raise HTTPException(status_code=404, detail="Conversation was not found.")
    return thread


@router.get("/threads/{thread_id}")
def thread_by_id(thread_id: UUID, response: Response) -> ThreadDetail:
    response.headers["Cache-Control"] = "no-store"
    try:
        thread = service().by_thread(thread_id)
    except (OSError, ValueError) as error:
        raise unavailable(error) from error
    if thread is None:
        raise HTTPException(status_code=404, detail="Conversation was not found.")
    return thread
