import json
from pathlib import Path
from uuid import UUID

from src.executor.agents.base import AgentRun
from src.executor.agents.claude.run import ClaudeRun
from src.shared.executor_protocol import ExecutorRequest

RUN = ClaudeRun(
    binary=Path("/opt/claude"), state_dir=Path("/data/state"), model="haiku", api_key="test-key",
    workspace=Path("/workspace/project"),
)
REQUEST = ExecutorRequest(workspace="/workspace/project", prompt="Secret prompt")


def option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_the_command_streams_events_and_keeps_prompt_and_key_out_of_the_arguments() -> None:
    run: AgentRun = RUN
    command = run.command(REQUEST)
    assert command[:2] == ("/opt/claude", "-p")
    assert option(command, "--output-format") == "stream-json"
    assert "--resume" not in command
    assert "test-key" not in " ".join(command) and "Secret prompt" not in " ".join(command)


def test_a_continuation_resumes_the_session() -> None:
    command = RUN.command(REQUEST.model_copy(update={"thread_id": UUID(int=7)}))
    assert option(command, "--resume") == str(UUID(int=7))


def test_nothing_runs_outside_the_sandbox_and_private_folders_are_unreadable() -> None:
    command = RUN.command(REQUEST)
    settings = json.loads(option(command, "--settings"))
    sandbox = settings["sandbox"]
    assert (sandbox["enabled"], sandbox["failIfUnavailable"], sandbox["allowUnsandboxedCommands"]) == (True, True, False)
    assert sandbox["filesystem"]["denyRead"] == ["/data/state", "/data/ipc"]
    assert settings["permissions"]["deny"] == ["Read(//data/state/**)", "Read(//data/ipc/**)"]
    assert option(command, "--permission-mode") == "dontAsk"


def test_the_process_receives_only_what_it_needs(monkeypatch) -> None:
    monkeypatch.setenv("UNRELATED_SECRET", "x")
    environment = RUN.environment()
    assert set(environment) == {"PATH", "LANG", "HOME", "ANTHROPIC_API_KEY"}
    assert environment["HOME"] == "/data/state"
