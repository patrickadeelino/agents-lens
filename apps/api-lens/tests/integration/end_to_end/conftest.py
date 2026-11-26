"""Start the backend and the executor around a scripted Codex."""

import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import httpx
import pytest

from src.backend.api import dependencies
from src.backend.main import app
from src.executor import main as executor
from src.executor.codex.catalog_run import CatalogRun
from src.executor.main import app as executor_app
from src.executor.workspace.workspace_selections import WorkspaceSelections
from tests.integration.end_to_end.fake_codex import PROBE
from tests.integration.end_to_end.support import WORKSPACE_ID, running_server


@pytest.fixture
def run_workspace(monkeypatch: pytest.MonkeyPatch, capture_root: Path) -> Iterator[Path]:
    with TemporaryDirectory(prefix="test-live-sse-", dir=capture_root) as path:
        workspace = Path(path)
        monkeypatch.setattr(dependencies, "CAPTURE_ROOT", workspace / "captures")
        monkeypatch.delenv("AGENT_LENS_EXECUTOR_SOCKET", raising=False)
        yield workspace


@pytest.fixture
def live_http() -> Iterator[tuple[httpx.Client, Event]]:
    request_finished = Event()
    with (
        running_server(app, request_finished) as url,
        httpx.Client(base_url=url, timeout=6, trust_env=False) as client,
    ):
        yield client, request_finished


@pytest.fixture
def run_config(run_workspace: Path) -> CatalogRun:
    example = run_workspace / "example"
    example.mkdir()
    stub = run_workspace / "fake-codex"
    stub.write_text(f"#!{sys.executable}\n" + PROBE, encoding="utf-8")
    stub.chmod(0o700)
    state = run_workspace / "state"
    state.mkdir()
    return CatalogRun(
        binary=stub, state_dir=state, workspace=example, workspace_root=run_workspace,
        model="test-model", api_key="not-a-real-key",
    )


@pytest.fixture
def executor_socket(
    monkeypatch: pytest.MonkeyPatch, run_workspace: Path, run_config: CatalogRun,
) -> Iterator[Path]:
    unix_path = run_workspace / "executor.sock"
    monkeypatch.setattr(executor_app.state, "catalog_run", run_config, raising=False)
    selections = WorkspaceSelections(new_id=lambda: WORKSPACE_ID)
    selections.remember(run_config.workspace)
    monkeypatch.setattr(executor, "workspace_selections", selections)
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(unix_path))
    with running_server(executor_app, Event(), unix_path=unix_path):
        yield unix_path
