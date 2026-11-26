from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.api import dependencies
from src.backend.infrastructure.demo_data import load_demo
from src.backend.main import app


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "captures"
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", root)
    monkeypatch.setenv("AGENT_LENS_DEMO", "1")
    monkeypatch.delenv("AGENT_LENS_EXECUTOR_SOCKET", raising=False)
    assert load_demo(root) == 2
    return root


def test_recordings_appear_as_saved_conversations(demo: Path) -> None:
    with TestClient(app, base_url="http://localhost") as client:
        threads = client.get("/threads").json()["threads"]
        details = [client.get(f"/threads/by-run/{thread['root_run_id']}").json() for thread in threads]
        histories = [client.get(f"/runs/{turn['run_id']}/events", params={"limit": 100})
                     for detail in details for turn in detail["turns"]]
    assert [len(detail["turns"]) for detail in details] == [2, 1]
    turns = [turn for detail in details for turn in detail["turns"]]
    assert {turn["status"] for turn in turns} == {"completed"}
    assert all(turn["assistant_markdown"] for turn in turns)
    assert turns[0]["summary"]["failures"] == 1
    assert {history.status_code for history in histories} == {200}
    events = [row["record"]["event"] for history in histories for row in history.json()["events"]]
    assert any(event["type"] == "delegation.edge" for event in events)


def test_loading_twice_keeps_one_copy(demo: Path) -> None:
    assert load_demo(demo) == 0
    assert len(list(demo.iterdir())) == 3


def test_demo_reports_its_mode_and_cannot_run_prompts(demo: Path) -> None:
    with TestClient(app, base_url="http://localhost") as client:
        ready = client.get("/ready")
        run = client.post("/runs", json={"prompt": "hello", "workspace_run_id": next(demo.iterdir()).name})
    assert ready.status_code == 204
    assert ready.headers["X-Agent-Lens-Mode"] == "demo"
    assert run.status_code == 503
    assert len(list(demo.iterdir())) == 3
