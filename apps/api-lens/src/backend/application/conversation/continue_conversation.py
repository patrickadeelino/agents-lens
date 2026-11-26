"""Accept the next prompt of a saved conversation."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Self
from uuid import UUID

from src.backend.application.conversation.conversation_ports import (
    ConversationRepository,
    ConversationRun,
)
from src.backend.application.conversation.conversations import Conversations
from src.backend.application.conversation.prompt_rules import (
    ExecutorGate,
    PromptRefused,
    create_run,
    reject_while_running,
    saved_session,
    saved_workspace,
)
from src.backend.application.conversation.run_continuation import (
    RunSession,
    conversation_thread,
    describe_continuation,
)
from src.shared.executor_protocol import AgentName


class ContinuableRun(ConversationRun, Protocol):
    def reserve_successor(self, successor: Self) -> None:
        """Link the next turn, raising FileExistsError when one is already linked."""
        ...

    def discard_unpublished(self) -> None: ...


class ContinuationStore[Run: ContinuableRun](ConversationRepository[Run], Protocol):
    def any_active(self) -> bool: ...
    def create(self, session: RunSession) -> Run: ...


@dataclass(frozen=True)
class AcceptedContinuation[Run]:
    run: Run
    thread_id: UUID
    workspace: str
    agent: AgentName


def reserve_turn[Run: ContinuableRun](origin: Run, output: Run) -> None:
    # The link is written once: of two concurrent prompts, only one gets it.
    try:
        origin.reserve_successor(output)
    except FileExistsError as error:
        output.discard_unpublished()
        raise PromptRefused("already_continued") from error
    except OSError as error:
        output.discard_unpublished()
        raise PromptRefused("capture_unavailable") from error


class ContinueConversation[Run: ContinuableRun]:
    def __init__(self, store: ContinuationStore[Run], executor: Callable[[], ExecutorGate]) -> None:
        self._store = store
        # Resolved only after the history checks, so an unusable run is reported first.
        self._executor = executor

    def execute(self, run_id: str, prompt: str) -> AcceptedContinuation[Run]:
        """Check every rule before saving, then link the new turn to the one it continues."""
        reject_while_running(self._store)
        origin, thread_id = self._resumable(run_id)
        # A session can only be resumed by the agent that created it.
        agent = saved_session(self._store.find, run_id).agent
        executor = self._executor()
        workspace = saved_workspace(self._store.find, run_id)
        executor.check_workspace(workspace)
        executor.check_agent(agent)
        output = create_run(self._store, RunSession(
            previous_run_id=run_id, prompt=prompt, submitted_at=datetime.now(UTC), workspace=workspace, agent=agent,
        ))
        reserve_turn(origin, output)
        return AcceptedContinuation(output, thread_id, workspace, agent)

    def _resumable(self, run_id: str) -> tuple[Run, UUID]:
        try:
            origin = self._store.find(run_id)
        except FileNotFoundError as error:
            raise PromptRefused("run_not_found") from error
        except OSError as error:
            raise PromptRefused("run_unreadable") from error
        try:
            # Opening the conversation validates its whole lineage and identity.
            Conversations(self._store).by_run(run_id)
            continuation = describe_continuation(run_id, origin, origin.read_result())
            thread_id = conversation_thread(origin) if continuation.state == "available" else None
        except (OSError, ValueError) as error:
            raise PromptRefused("run_unreadable") from error
        if thread_id is None:
            raise PromptRefused("not_continuable", continuation.reason)
        return origin, thread_id
