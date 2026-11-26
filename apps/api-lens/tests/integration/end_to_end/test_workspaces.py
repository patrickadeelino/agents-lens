"""Choose the folder a run may use."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import httpx
import pytest

from src.executor import main as executor
from src.executor.agents.codex.catalog_run import CatalogRun
from src.executor.workspace.workspace_selections import WorkspaceSelections
from tests.integration.end_to_end.support import prompt_body


@pytest.mark.usefixtures("executor_socket")
def test_a_chosen_folder_runs_in_the_original_directory(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = run_workspace / "projects" / "original project"
    directory.mkdir(parents=True)
    monkeypatch.setattr(executor, "workspace_selections", WorkspaceSelections())
    backend, _ = live_http
    listing = backend.get("/folders", params={"path": "projects"})
    assert listing.json() == {
        "root": str(run_workspace), "path": "projects", "folders": ["original project"], "truncated": False,
    }
    assert backend.get("/folders", params={"path": "../"}).status_code == 409
    choice = {"path": "projects/original project"}
    assert backend.post("/folders", json=choice).status_code == 422
    header = {"X-Agent-Lens": "folder-selection"}
    assert backend.post("/folders", json={"path": ".."}, headers=header).status_code == 409
    selection = backend.post("/folders", json=choice, headers=header)
    assert selection.status_code == 200
    assert selection.json()["path"] == str(directory)

    assert backend.post("/runs", json={"prompt": "catalog first"}).status_code == 422
    assert backend.post("/runs", json={"prompt": "catalog first", "workspace": str(directory)}).status_code == 422
    first = backend.post("/runs", json={"prompt": "catalog first", "workspace_id": selection.json()["id"]})
    assert first.status_code == 200
    first_id = first.headers["X-Run-ID"]
    assert (directory / "catalog.txt").read_text() == "category filtering\n"
    assert not (run_workspace / "example" / "catalog.txt").exists()
    assert backend.get(f"/threads/by-run/{first_id}").json()["workspace"] == str(directory)
    assert backend.get("/threads").json()["threads"][0]["workspace"] == str(directory)

    # A restarted executor forgets every selection.
    monkeypatch.setattr(executor, "workspace_selections", WorkspaceSelections())
    expired = backend.post("/runs", json={"prompt": "catalog first", "workspace_id": selection.json()["id"]})
    assert expired.status_code == 409
    second = backend.post(f"/runs/{first_id}/continue", json={"prompt": "catalog second"})
    assert second.status_code == 200
    assert (directory / "catalog.txt").read_text() == "category filtering\npagination\n"
    new = backend.post("/runs", json={"prompt": "independent conversation", "workspace_run_id": first_id})
    assert new.status_code == 200
    detail = backend.get(f"/threads/by-run/{new.headers['X-Run-ID']}").json()
    assert detail["workspace"] == str(directory)
    assert detail["root_run_id"] == new.headers["X-Run-ID"]
    assert len(detail["turns"]) == 1


@pytest.mark.usefixtures("executor_socket")
def test_readiness_checks_the_host_executor(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = live_http
    assert backend.get("/ready").status_code == 204
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "missing.sock"))
    assert backend.get("/ready").status_code == 503
    assert backend.get("/threads").json() == {"threads": [], "unavailable": 0}


@pytest.mark.usefixtures("executor_socket")
def test_a_saved_folder_outside_the_root_cannot_be_used(
    live_http: tuple[httpx.Client, Event], run_config: CatalogRun, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = live_http
    first = backend.post("/runs", json=prompt_body("save marker: niobium")).headers["X-Run-ID"]
    narrower = run_config.workspace_root / "elsewhere"
    narrower.mkdir()
    narrowed = replace(executor.configured_setup(), workspace_root=narrower)
    monkeypatch.setattr(executor, "configured_setup", lambda: narrowed)
    follow_up = backend.post(f"/runs/{first}/continue", json={"prompt": "recall the marker"})
    assert follow_up.status_code == 422
    sibling = backend.post("/runs", json={"prompt": "new conversation", "workspace_run_id": first})
    assert sibling.status_code == 422
    assert len(backend.get("/threads").json()["threads"]) == 1


@pytest.mark.usefixtures("executor_socket")
def test_unavailable_workspace_does_not_reserve_a_continuation(
    live_http: tuple[httpx.Client, Event], run_config: CatalogRun,
) -> None:
    backend, _ = live_http
    first = backend.post("/runs", json=prompt_body("save marker: niobium"))
    origin = first.headers["X-Run-ID"]
    follow_up = f"/runs/{origin}/continue"
    moved = run_config.workspace.with_name("workspace-moved")
    run_config.workspace.rename(moved)
    try:
        rejected = backend.post(follow_up, json={"prompt": "recall the marker"})
        assert rejected.status_code == 422
        assert "Restore the folder" in rejected.json()["detail"]
        assert "X-Run-ID" not in rejected.headers
        detail = backend.get(f"/threads/by-run/{origin}").json()
        assert detail["continuation"]["state"] == "available"
        assert len(detail["turns"]) == 1
    finally:
        moved.rename(run_config.workspace)

    resumed = backend.post(follow_up, json={"prompt": "recall the marker"})
    assert resumed.status_code == 200
    detail = backend.get(f"/threads/by-run/{resumed.headers['X-Run-ID']}").json()
    assert detail["root_run_id"] == origin
    assert detail["turns"][1]["assistant_markdown"] == "niobium"
