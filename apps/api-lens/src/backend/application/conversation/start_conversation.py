"""Accept the first prompt of a new conversation."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from src.shared.executor_protocol import DEFAULT_AGENT, AgentName

from .prompt_rules import (
    ActivityLog,
    ExecutorGate,
    PromptRefused,
    RunCreator,
    SessionReader,
    create_run,
    reject_while_running,
    saved_workspace,
)
from .run_continuation import RunSession


@dataclass(frozen=True)
class SelectedFolder:
    """A folder the person has just chosen."""

    selection_id: UUID


@dataclass(frozen=True)
class FolderOfRun:
    """The folder already used by a saved conversation."""

    run_id: str


type FolderChoice = SelectedFolder | FolderOfRun


class FolderGate(ExecutorGate, Protocol):
    def selected_folder(self, selection_id: UUID) -> str:
        """Return the chosen path, raising PromptRefused when the selection cannot be used."""
        ...


class NewRunStore[Run](RunCreator[Run], ActivityLog, Protocol):
    def find(self, run_id: str) -> SessionReader: ...


@dataclass(frozen=True)
class StartedConversation[Run]:
    run: Run
    workspace: str


class StartConversation[Run]:
    def __init__(self, store: NewRunStore[Run], executor: Callable[[], FolderGate]) -> None:
        self._store = store
        self._executor = executor

    def execute(
        self, prompt: str, folder: FolderChoice | None, agent: AgentName = DEFAULT_AGENT,
    ) -> StartedConversation[Run]:
        """Settle the folder and the agent before saving anything."""
        reject_while_running(self._store)
        executor = self._executor()
        workspace = self._workspace(executor, folder)
        executor.check_agent(agent)
        run = create_run(self._store, RunSession(
            prompt=prompt, submitted_at=datetime.now(UTC), workspace=workspace, agent=agent,
        ))
        return StartedConversation(run, workspace)

    def _workspace(self, executor: FolderGate, folder: FolderChoice | None) -> str:
        if folder is None:
            raise PromptRefused("folder_required")
        if isinstance(folder, SelectedFolder):
            return executor.selected_folder(folder.selection_id)
        # A folder saved earlier may have moved since then.
        workspace = saved_workspace(self._store.find, folder.run_id)
        executor.check_workspace(workspace)
        return workspace
