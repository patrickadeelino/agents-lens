from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from src.backend.application.conversation.prompt_rules import PromptRefused
from src.backend.application.conversation.run_continuation import RunSession
from src.backend.application.conversation.start_conversation import (
    FolderOfRun,
    SelectedFolder,
    StartConversation,
)
from src.backend.infrastructure.storage.conversation_store import ConversationStore

SELECTION = UUID("11111111-1111-4111-8111-111111111111")


class Executor:
    def __init__(self) -> None:
        self.checked: list[str] = []
        self.agents: list[str] = []

    def check_agent(self, agent: str) -> None:
        self.agents.append(agent)
        if agent != "codex":
            raise PromptRefused("agent_unavailable")

    def selected_folder(self, selection_id: UUID) -> str:
        assert selection_id == SELECTION
        return "/work/chosen"

    def check_workspace(self, workspace: str) -> None:
        self.checked.append(workspace)


def saved_folder(store: ConversationStore, prompt: str, folder) -> tuple[str, list[str]]:
    executor = Executor()
    started = StartConversation(store, lambda: executor).execute(prompt, folder)
    session = started.run.read_session()
    assert session is not None
    assert (session.prompt, session.workspace) == (prompt, started.workspace)
    return started.workspace, executor.checked


def test_a_selected_folder_is_saved_with_the_new_run(tmp_path: Path) -> None:
    assert saved_folder(ConversationStore(tmp_path), "hello", SelectedFolder(SELECTION)) == ("/work/chosen", [])


def test_the_folder_of_a_saved_run_is_checked_again(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    earlier = store.create(RunSession(prompt="earlier", workspace="/work/old", submitted_at=datetime.now(UTC)))
    assert saved_folder(store, "again", FolderOfRun(earlier.run_id)) == ("/work/old", ["/work/old"])


@pytest.mark.parametrize(("folder", "refusal"), [
    (None, "folder_required"), (FolderOfRun("codex-prompt-missing"), "conversation_not_found"),
])
def test_a_prompt_without_a_usable_folder_saves_nothing(tmp_path: Path, folder, refusal: str) -> None:
    with pytest.raises(PromptRefused, match=refusal):
        StartConversation(ConversationStore(tmp_path), Executor).execute("hello", folder)
    assert list(tmp_path.iterdir()) == []


def test_an_unavailable_agent_saves_nothing(tmp_path: Path) -> None:
    use_case = StartConversation(ConversationStore(tmp_path), Executor)
    with pytest.raises(PromptRefused, match="agent_unavailable"):
        use_case.execute("hello", SelectedFolder(SELECTION), "claude_code")
    assert list(tmp_path.iterdir()) == []


def test_a_conversation_saved_before_the_choice_belongs_to_codex() -> None:
    saved = '{"prompt": "old", "workspace": "/work", "submitted_at": "2026-11-20T10:00:00Z"}'
    assert RunSession.model_validate_json(saved).agent == "codex"
