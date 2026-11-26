"""Derive whether a saved run can start another turn in its Codex session."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.backend.domain.conversation.lineage import LinkedRun, ancestors, descendants


class RunSession(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    prompt: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    submitted_at: datetime
    previous_run_id: str | None = None


class RecordedResult(Protocol):
    exit_code: int | None
    thread_id: UUID | None
    conflicting_thread_id: UUID | None
    capture_failed: bool


class SavedConversationRun(LinkedRun, Protocol):
    def read_session(self) -> RunSession | None: ...
    def read_result(self) -> RecordedResult | None: ...
    def is_active(self) -> bool: ...


type ContinuationState = Literal["available", "active", "superseded", "unavailable"]


@dataclass(frozen=True)
class ContinuationInfo:
    state: ContinuationState
    reason: str | None = None
    previous_run_id: str | None = None
    latest_run_id: str | None = None


def conversation_thread(capture: SavedConversationRun) -> UUID | None:
    """Return the thread confirmed by this run or its nearest confirmed ancestor."""
    results = (run.read_result() for run in ancestors(capture))
    return next((result.thread_id for result in results if result and result.thread_id), None)


def describe_continuation(
    run_id: str, capture: SavedConversationRun, result: RecordedResult | None,
) -> ContinuationInfo:
    session = capture.read_session()
    if session is None:
        return ContinuationInfo("unavailable", "This run has no saved session.")
    *_, last = descendants(capture)
    latest = last.run_id
    state, reason = _eligibility(run_id, latest, capture, result)
    return ContinuationInfo(state, reason, session.previous_run_id, latest)


def _eligibility(
    run_id: str, latest: str, capture: SavedConversationRun, result: RecordedResult | None,
) -> tuple[ContinuationState, str | None]:
    if latest != run_id:
        return "superseded", "Open the latest run to continue this conversation."
    if result is not None and result.exit_code is not None and result.thread_id is not None:
        return "available", None
    if capture.is_active():
        return "active", "Wait for this run to finish."
    # A finished attempt that never confirmed its session resumes the thread its lineage confirmed.
    if result is not None and result.conflicting_thread_id is None and conversation_thread(capture) is not None:
        return "available", None
    return "unavailable", "Codex did not confirm a saved session for this run."
