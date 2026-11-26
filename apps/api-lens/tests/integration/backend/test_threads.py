from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.backend.api import dependencies
from src.backend.application.conversation.run_continuation import RunSession
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.main import app


def saved(root: Path, prompt: str, thread: str, when: datetime) -> CaptureFiles:
    capture = CaptureFiles.create(root, prefix="codex-prompt-")
    capture.write_session(RunSession(prompt=prompt, workspace="/work", submitted_at=when))
    capture.ensure_trace()
    capture.write_result(0, thread_id=UUID(thread),
                         assistant_markdown="Reply", last_turn="turn.completed", finished_at=when)
    return capture


def test_listing_reads_metadata_and_orders_conversations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    first = saved(tmp_path, "first   prompt", "11111111-1111-4111-8111-111111111111", datetime(2026, 1, 1, tzinfo=UTC))
    second = saved(tmp_path, "second prompt", "22222222-2222-4222-8222-222222222222", datetime(2026, 1, 2, tzinfo=UTC))
    def no_trace(*args, **kwargs):
        raise AssertionError("Home must not read traces")
    monkeypatch.setattr(CaptureFiles, "read_trace_lines", no_trace)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/threads")
    assert response.status_code == 200
    threads = response.json()["threads"]
    assert [thread["root_run_id"] for thread in threads] == [second.directory.name, first.directory.name]
    assert threads[1]["title"] == "first prompt"


@pytest.mark.parametrize(("name", "contents"), [
    ("result.json", '{"exit_c'), ("session.json", '{"pers'), ("next-run", "codex-prompt-gone"),
])
def test_a_damaged_conversation_does_not_take_down_the_others(
    tmp_path: Path, monkeypatch, name: str, contents: str,
) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    good = saved(tmp_path, "good", "11111111-1111-4111-8111-111111111111", datetime(2026, 1, 1, tzinfo=UTC))
    bad = saved(tmp_path, "bad", "22222222-2222-4222-8222-222222222222", datetime(2026, 1, 2, tzinfo=UTC))
    (bad.directory / name).write_text(contents, encoding="utf-8")
    with TestClient(app, base_url="http://localhost") as client:
        listing = client.get("/threads")
        assert listing.status_code == 200
        assert [thread["root_run_id"] for thread in listing.json()["threads"]] == [good.directory.name]
        assert listing.json()["unavailable"] == 1
        assert client.get(f"/threads/by-run/{good.directory.name}").status_code == 200
        assert client.get("/threads/11111111-1111-4111-8111-111111111111").status_code == 200
        assert client.get("/threads/22222222-2222-4222-8222-222222222222").status_code == 503
        assert client.get(f"/threads/by-run/{bad.directory.name}").status_code == 503
        assert client.post(f"/runs/{bad.directory.name}/continue", json={"prompt": "Next"}).status_code == 503
        # Without an executor, reaching that check proves the good conversation passed the history checks.
        monkeypatch.delenv("AGENT_LENS_EXECUTOR_SOCKET", raising=False)
        continued = client.post(f"/runs/{good.directory.name}/continue", json={"prompt": "Next"})
        assert continued.json()["detail"] == "Executor socket is not configured."


def test_a_damaged_later_turn_counts_its_conversation_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    first = saved(tmp_path, "first", "11111111-1111-4111-8111-111111111111", datetime(2026, 1, 1, tzinfo=UTC))
    second = saved(tmp_path, "second", "11111111-1111-4111-8111-111111111111", datetime(2026, 1, 2, tzinfo=UTC))
    first.reserve_successor(second)
    (second.directory / "session.json").write_text("{", encoding="utf-8")
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/threads").json() == {"threads": [], "unavailable": 1}


def test_conversations_sharing_an_identity_are_left_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    shared = "11111111-1111-4111-8111-111111111111"
    first = saved(tmp_path, "first", shared, datetime(2026, 1, 1, tzinfo=UTC))
    saved(tmp_path, "second", shared, datetime(2026, 1, 2, tzinfo=UTC))
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/threads").json() == {"threads": [], "unavailable": 2}
        assert client.get(f"/threads/by-run/{first.directory.name}").status_code == 503
        assert client.get(f"/threads/{shared}").status_code == 503


def test_bad_successor_does_not_merge_conversations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    first = saved(tmp_path, "first", "11111111-1111-4111-8111-111111111111", datetime(2026, 1, 1, tzinfo=UTC))
    second = saved(tmp_path, "second", "22222222-2222-4222-8222-222222222222", datetime(2026, 1, 2, tzinfo=UTC))
    first.reserve_successor(second)
    with TestClient(app, base_url="http://localhost") as client:
        listing = client.get("/threads").json()
        assert [thread["root_run_id"] for thread in listing["threads"]] == [second.directory.name]
        assert listing["unavailable"] == 1
        assert client.get(f"/threads/by-run/{first.directory.name}").status_code == 503
        assert client.post(f"/runs/{first.directory.name}/continue", json={"prompt": "Next"}).status_code == 503


def test_opening_by_thread_reads_each_result_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", tmp_path)
    thread = "11111111-1111-4111-8111-111111111111"
    first = saved(tmp_path, "first", thread, datetime(2026, 1, 1, tzinfo=UTC))
    second = saved(tmp_path, "second", thread, datetime(2026, 1, 2, tzinfo=UTC))
    first.reserve_successor(second)
    second_session = second.read_session()
    assert second_session is not None
    (second.directory / "session.json").write_text(
        second_session.model_copy(update={"previous_run_id": first.run_id}).model_dump_json(), encoding="utf-8")
    reads: list[str] = []
    read_result = CaptureFiles.read_result

    def counted(capture: CaptureFiles):
        reads.append(capture.run_id)
        return read_result(capture)

    monkeypatch.setattr(CaptureFiles, "read_result", counted)
    with TestClient(app, base_url="http://localhost") as client:
        reads.clear()  # Startup recovery reads the results too.
        assert client.get(f"/threads/{thread}").status_code == 200
    # One pass to find the thread and one to load its details.
    assert sorted(reads) == sorted([first.run_id, second.run_id] * 2)

