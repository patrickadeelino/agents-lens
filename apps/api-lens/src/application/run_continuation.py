"""Derive whether a saved run can start another turn in its Codex session."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunSession(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    persistent: bool
    previous_run_id: str | None = None
    prompt: str | None = None
    submitted_at: datetime | None = None


class RecordedResult(Protocol):
    exit_code: int | None
    thread_id: UUID | None
    persistence_confirmed: bool


class SavedConversationRun(Protocol):
    def read_session(self) -> RunSession | None: ...
    def latest_run_id(self) -> str: ...
    def is_active(self) -> bool: ...


type ContinuationState = Literal["available", "active", "superseded", "ephemeral", "unavailable"]


@dataclass(frozen=True)
class ContinuationInfo:
    state: ContinuationState
    reason: str | None = None
    previous_run_id: str | None = None
    latest_run_id: str | None = None


def describe_continuation(
    run_id: str, capture: SavedConversationRun, result: RecordedResult | None,
) -> ContinuationInfo:
    session = capture.read_session()
    if session is None or not session.persistent:
        return ContinuationInfo("ephemeral", "This run did not save a Codex session.")
    latest = capture.latest_run_id()
    state, reason = _eligibility(run_id, latest, capture, result)
    return ContinuationInfo(state, reason, session.previous_run_id, latest)


def _eligibility(
    run_id: str, latest: str, capture: SavedConversationRun, result: RecordedResult | None,
) -> tuple[ContinuationState, str | None]:
    if latest != run_id:
        return "superseded", "Open the latest run to continue this conversation."
    if result is not None and result.exit_code is not None and result.thread_id is not None and result.persistence_confirmed:
        return "available", None
    if capture.is_active():
        return "active", "Wait for this run to finish."
    return "unavailable", "Codex did not confirm a saved session for this run."
