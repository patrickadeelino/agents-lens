from pathlib import Path

import pytest
from fastapi import HTTPException

from src.executor.agents.codex.adapter import codex_agent
from src.executor.agents.codex.catalog_run import CatalogRun, ExecutorConfig
from src.executor.agents.configured import ExecutorSetup
from src.executor.main import app, configured_agent, configured_setup


def codex_config(root: Path) -> ExecutorConfig:
    return ExecutorConfig(
        binary=root / "codex", state_dir=root / "state", model="test-model", api_key="k", workspace_root=root,
    )


def test_the_codex_agent_runs_in_the_requested_folder(tmp_path: Path) -> None:
    agent = codex_agent(codex_config(tmp_path))
    run = agent.run_in(tmp_path / "project")
    assert isinstance(run, CatalogRun)
    assert (agent.adapter.name, run.workspace, run.model) == ("codex", tmp_path / "project", "test-model")


def test_only_configured_agents_can_be_used(tmp_path: Path) -> None:
    setup = ExecutorSetup(tmp_path, tmp_path / "state", {"codex": codex_agent(codex_config(tmp_path))})
    assert configured_agent(setup, "codex") is setup.agents["codex"]
    with pytest.raises(HTTPException) as refused:
        configured_agent(setup, "claude_code")
    assert refused.value.status_code == 409


def test_an_executor_without_agents_is_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "setup", ExecutorSetup(tmp_path, tmp_path, {}), raising=False)
    with pytest.raises(HTTPException) as refused:
        configured_setup()
    assert refused.value.status_code == 503
