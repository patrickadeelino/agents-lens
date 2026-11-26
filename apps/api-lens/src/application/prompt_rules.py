"""Decide whether a prompt may be accepted, before anything about it is saved."""

from typing import Literal, Protocol

from src.observability import event_log

from .run_continuation import RunSession

type Refusal = Literal[
    "executor_busy", "run_not_found", "run_unreadable", "not_continuable", "already_continued",
    "conversation_not_found", "history_unavailable", "folder_missing",
    "workspace_rejected", "executor_unavailable", "capture_unavailable",
]


class PromptRefused(Exception):
    def __init__(self, refusal: Refusal, reason: str | None = None) -> None:
        super().__init__(refusal)
        self.refusal = refusal
        self.reason = reason


class WorkspaceGate(Protocol):
    def check_workspace(self, workspace: str) -> None:
        """Raise PromptRefused when the folder cannot be used."""
        ...


class SessionReader(Protocol):
    def read_session(self) -> RunSession | None: ...


class FindSession(Protocol):
    def __call__(self, run_id: str) -> SessionReader: ...


class ActivityLog(Protocol):
    def any_active(self) -> bool: ...


def reject_while_running(runs: ActivityLog) -> None:
    """Refuse a prompt while the single executor is running another one."""
    # Not atomic: two simultaneous prompts can pass, and the executor still rejects the second.
    if runs.any_active():
        event_log("run.rejected", "api", "rejected", error_code="executor_busy")
        raise PromptRefused("executor_busy")


def saved_workspace(find: FindSession, run_id: str) -> str:
    try:
        session = find(run_id).read_session()
    except FileNotFoundError as error:
        raise PromptRefused("conversation_not_found") from error
    except (OSError, ValueError) as error:
        raise PromptRefused("history_unavailable") from error
    if session is None:
        raise PromptRefused("folder_missing")
    return session.workspace


class RunCreator[Run](Protocol):
    def create(self, session: RunSession) -> Run: ...


def create_run[Run](runs: RunCreator[Run], session: RunSession) -> Run:
    try:
        return runs.create(session)
    except OSError as error:
        event_log("run.rejected", "api", "rejected", error_code="capture_unavailable")
        raise PromptRefused("capture_unavailable") from error
