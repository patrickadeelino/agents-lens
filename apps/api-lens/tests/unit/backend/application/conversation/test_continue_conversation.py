from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from src.backend.application.conversation.continue_conversation import (
    ContinueConversation,
)
from src.backend.application.conversation.prompt_rules import PromptRefused
from src.backend.application.conversation.run_continuation import RunSession
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.infrastructure.storage.conversation_store import ConversationStore

THREAD = UUID("11111111-1111-4111-8111-111111111111")


class Gate:
    def __init__(self, on_check=lambda: None) -> None:
        self.on_check = on_check
        self.agents: list[str] = []

    def check_agent(self, agent: str) -> None:
        self.agents.append(agent)

    def check_workspace(self, workspace: str) -> None:
        self.on_check()


def finished_run(store: ConversationStore) -> CaptureFiles:
    run = store.create(RunSession(prompt="first", workspace="/work", submitted_at=datetime.now(UTC)))
    run.write_result(0, thread_id=THREAD)
    return run


def test_the_accepted_turn_is_linked_to_the_one_it_continues(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    origin = finished_run(store)
    accepted = ContinueConversation(store, Gate).execute(origin.run_id, "next")
    assert (accepted.thread_id, accepted.workspace) == (THREAD, "/work")
    assert origin.successor() == accepted.run


def test_a_refused_folder_saves_nothing(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    origin = finished_run(store)

    def refuse() -> None:
        raise PromptRefused("workspace_rejected")

    with pytest.raises(PromptRefused, match="workspace_rejected"):
        ContinueConversation(store, lambda: Gate(refuse)).execute(origin.run_id, "next")
    assert list(tmp_path.iterdir()) == [origin.directory]
    assert origin.successor() is None


def test_losing_the_link_to_a_concurrent_prompt_discards_the_new_turn(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    origin = finished_run(store)
    rivals: list[CaptureFiles] = []

    def rival_links_first() -> None:
        rivals.append(store.create(RunSession(
            prompt="rival", workspace="/work", submitted_at=datetime.now(UTC), previous_run_id=origin.run_id,
        )))
        origin.reserve_successor(rivals[0])

    with pytest.raises(PromptRefused, match="already_continued"):
        ContinueConversation(store, lambda: Gate(rival_links_first)).execute(origin.run_id, "next")
    assert sorted(tmp_path.iterdir()) == sorted([origin.directory, rivals[0].directory])


def test_the_new_turn_uses_the_agent_that_owns_the_session(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    origin = store.create(RunSession(
        prompt="first", workspace="/work", submitted_at=datetime.now(UTC), agent="claude_code",
    ))
    origin.write_result(0, thread_id=THREAD)
    gate = Gate()
    accepted = ContinueConversation(store, lambda: gate).execute(origin.run_id, "next")
    session = accepted.run.read_session()
    assert session is not None
    assert (gate.agents, accepted.agent, session.agent) == (["claude_code"], "claude_code", "claude_code")


def test_a_conversation_mixing_agents_is_refused(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    origin = finished_run(store)
    other = store.create(RunSession(
        prompt="next", workspace="/work", submitted_at=datetime.now(UTC), previous_run_id=origin.run_id,
        agent="claude_code",
    ))
    origin.reserve_successor(other)
    with pytest.raises(PromptRefused, match="run_unreadable"):
        ContinueConversation(store, Gate).execute(other.run_id, "third")
