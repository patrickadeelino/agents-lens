from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from src.backend.api import dependencies
from src.backend.application.conversation.run_continuation import RunSession
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.main import app

THREAD = UUID("11111111-1111-4111-8111-111111111111")


def saved(root: Path, previous: CaptureFiles | None = None) -> CaptureFiles:
    capture = CaptureFiles.create(root, prefix="codex-prompt-")
    capture.write_session(RunSession(
        prompt="hello", workspace="/work", submitted_at=datetime.now(UTC),
        previous_run_id=previous.run_id if previous else None,
    ))
    if previous:
        previous.reserve_successor(capture)
    return capture


def continuation(client: TestClient, run: CaptureFiles) -> tuple[str, str]:
    detail = client.get(f"/threads/by-run/{run.run_id}").json()
    return detail["status"], detail["continuation"]["state"]


def test_a_turn_interrupted_by_a_restart_can_be_continued(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    first = saved(tmp_path)
    first.write_result(0, thread_id=THREAD, last_turn="turn.completed")
    interrupted = saved(tmp_path, first)
    finished = (first.directory / "result.json").read_bytes()
    with TestClient(app, base_url="http://localhost") as client:
        assert continuation(client, interrupted) == ("unconfirmed", "available")
        assert client.get(f"/runs/{interrupted.run_id}/events").status_code == 200
    assert (first.directory / "result.json").read_bytes() == finished


def test_an_interrupted_first_turn_has_no_session_to_resume(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    only = saved(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert continuation(client, only) == ("unconfirmed", "unavailable")


def test_a_damaged_capture_does_not_stop_the_startup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    damaged = saved(tmp_path)
    (damaged.directory / "session.json").write_text("{", encoding="utf-8")
    emptied = saved(tmp_path)
    (emptied.directory / "result.json").write_text("", encoding="utf-8")
    healthy = saved(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/threads").status_code == 200
    assert healthy.read_result() is not None
    assert not (damaged.directory / "result.json").exists()
