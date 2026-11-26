"""Choose the agent of a conversation through both services."""

import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import httpx
import pytest

from src.executor import main as executor
from src.executor.agents.configured import ExecutorSetup
from src.shared.executor_protocol import AgentName
from tests.integration.end_to_end.support import capture_directory, prompt_body


def configure(*names: AgentName) -> None:
    """Offer the scripted Codex under each name, so only the routing differs.

    The executor fixture removes the setup when the test ends.
    """
    setup: ExecutorSetup = executor.configured_setup()
    codex = next(iter(setup.agents.values()))
    executor.app.state.setup = replace(setup, agents={name: codex for name in names})


@pytest.mark.usefixtures("executor_socket")
def test_the_browser_learns_which_agents_can_run(live_http: tuple[httpx.Client, Event]) -> None:
    backend, _ = live_http
    assert backend.get("/agents").json() == {"agents": ["codex"]}


def test_the_agents_are_unknown_without_an_executor(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = live_http
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "missing.sock"))
    assert backend.get("/agents").status_code == 503


@pytest.mark.usefixtures("executor_socket")
def test_an_unconfigured_agent_saves_nothing(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    refused = backend.post("/runs", json=prompt_body("hello") | {"agent": "claude_code"})
    assert (refused.status_code, refused.json()) == (409, {"detail": "This agent is not configured."})
    assert not list((run_workspace / "captures").glob("*"))
    assert backend.post("/runs", json=prompt_body("hello") | {"agent": "other"}).status_code == 422


@pytest.mark.usefixtures("executor_socket")
def test_a_conversation_keeps_its_agent_when_continued(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    configure("codex", "claude_code")
    first = backend.post("/runs", json=prompt_body("save marker: niobium") | {"agent": "claude_code"})
    origin = first.headers["X-Run-ID"]
    saved = json.loads((capture_directory(run_workspace) / "session.json").read_text(encoding="utf-8"))
    assert saved["agent"] == "claude_code"

    configure("codex")
    refused = backend.post(f"/runs/{origin}/continue", json={"prompt": "recall the marker"})
    assert refused.status_code == 409

    configure("claude_code")
    accepted = backend.post(f"/runs/{origin}/continue", json={"prompt": "recall the marker"})
    assert accepted.status_code == 200
    detail = backend.get(f"/threads/by-run/{origin}").json()
    assert (detail["agent"], len(detail["turns"])) == ("claude_code", 2)
    assert [thread["agent"] for thread in backend.get("/threads").json()["threads"]] == ["claude_code"]
