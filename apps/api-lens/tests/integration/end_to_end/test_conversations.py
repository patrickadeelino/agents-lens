"""Continue conversations, including the attempts that fail."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import httpx
import pytest

from src.backend.infrastructure import executor_client
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.backend.main import app
from tests.integration.end_to_end.support import (
    LITERAL_PROMPT,
    next_frame,
    prompt_body,
    running_server,
)


@pytest.mark.usefixtures("executor_socket")
def test_latest_run_resumes_the_thread_once_and_links_survive_api_restart(run_workspace: Path) -> None:
    thread_id = "c47f8d99-9af1-4219-bc65-a55a27096cb8"
    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as backend:
        first = backend.post("/runs", json=prompt_body("save marker: niobium"))
        first_id = first.headers["X-Run-ID"]
        original = f"/runs/{first_id}"
        assert thread_id in first.text
        assert backend.get(original + "/events").json()["continuation"]["state"] == "available"
        assert backend.post(original + "/continue", json={"prompt": "hello", "thread_id": thread_id}).status_code == 422

        with ThreadPoolExecutor(max_workers=2) as pool:
            sends = [pool.submit(backend.post, original + "/continue", json={"prompt": "recall the marker"})
                     for _ in range(2)]
            accepted, rejected = sorted((send.result() for send in sends), key=lambda response: response.status_code)

        assert (accepted.status_code, rejected.status_code) == (200, 409)
        assert "X-Run-ID" not in rejected.headers
        second_id = accepted.headers["X-Run-ID"]
        assert second_id != first_id
        assert thread_id in accepted.text
        assert "niobium" in accepted.text
        records = [json.loads(line[6:]) for line in accepted.text.splitlines() if line.startswith("data: ")]
        assert records[-2]["event"]["usage"] == {"input_tokens": 25, "cached_input_tokens": 7, "output_tokens": 5}
        assert (run_workspace / "state" / "resume-attempts").read_text().splitlines() == ["recall the marker"]
        assert len(list((run_workspace / "captures").iterdir())) == 2
        first_session = CaptureFiles.find(run_workspace / "captures", first_id).read_session()
        second_session = CaptureFiles.find(run_workspace / "captures", second_id).read_session()
        assert first_session is not None and second_session is not None
        assert first_session.prompt == "save marker: niobium"
        assert second_session.prompt == "recall the marker"
        assert first_session.submitted_at is not None and second_session.submitted_at is not None
        assert first_session.submitted_at.tzinfo == UTC
        assert second_session.submitted_at.tzinfo == UTC
        listing = backend.get("/threads")
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        assert len(listing.json()["threads"]) == 1
        summary = listing.json()["threads"][0]
        assert summary["thread_id"] == thread_id
        assert summary["root_run_id"] == first_id
        assert summary["latest_run_id"] == second_id
        detail = backend.get(f"/threads/by-run/{second_id}").json()
        assert [turn["prompt"] for turn in detail["turns"]] == ["save marker: niobium", "recall the marker"]
        assert detail["turns"][1]["assistant_markdown"] == "niobium"
        assert [turn["summary"]["actions"] for turn in detail["turns"]] == [0, 0]
        assert all(turn["summary"]["failures"] == 0 for turn in detail["turns"])
        assert backend.get(f"/threads/{thread_id}").json() == detail
        independent = backend.post("/runs", json=prompt_body("independent conversation"))
        independent_id = independent.headers["X-Run-ID"]
        listing = backend.get("/threads").json()["threads"]
        assert [row["root_run_id"] for row in listing] == [independent_id, first_id]

    with running_server(app, Event()) as url, httpx.Client(base_url=url, timeout=6, trust_env=False) as backend:
        restored = backend.get("/threads").json()["threads"]
        assert len(restored) == 2
        assert restored[1]["root_run_id"] == first_id
        assert restored[1]["latest_run_id"] == second_id
        assert backend.get(f"/threads/{thread_id}").json()["turns"] == detail["turns"]
        old = backend.get(f"/runs/{first_id}/events").json()
        new = backend.get(f"/runs/{second_id}/events").json()
        assert old["continuation"]["state"] == "superseded"
        assert old["continuation"]["latest_run_id"] == second_id
        assert new["continuation"]["previous_run_id"] == first_id
        assert new["continuation"]["state"] == "available"
        for page in (old, new):
            assert page["events"][0]["record"]["event"]["thread_id"] == thread_id


@pytest.mark.usefixtures("executor_socket")
def test_controlled_turns_edit_same_workspace(live_http: tuple[httpx.Client, Event], run_workspace: Path) -> None:
    backend, _ = live_http
    first = backend.post("/runs", json=prompt_body("catalog first"))
    first_id = first.headers["X-Run-ID"]
    second = backend.post(f"/runs/{first_id}/continue", json={"prompt": "catalog second"})
    second_id = second.headers["X-Run-ID"]
    assert (run_workspace / "example" / "catalog.txt").read_text() == "category filtering\npagination\n"
    detail = backend.get(f"/threads/by-run/{second_id}").json()
    assert [turn["run_id"] for turn in detail["turns"]] == [first_id, second_id]
    assert detail["thread_id"] == "c47f8d99-9af1-4219-bc65-a55a27096cb8"


@pytest.mark.usefixtures("executor_socket")
def test_continuation_reopens_a_failed_attempt_without_events(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    assert backend.post("/runs/missing/continue", json={"prompt": "hello"}).status_code == 404

    first = backend.post("/runs", json=prompt_body("save marker: niobium"))
    first_id = first.headers["X-Run-ID"]
    (run_workspace / "state" / "reject-resume").touch()
    failed = backend.post(f"/runs/{first_id}/continue", json={"prompt": "reject the continuation"})
    assert failed.status_code == 200
    failed_id = failed.headers["X-Run-ID"]
    response = backend.get(f"/runs/{failed_id}/events")
    assert response.status_code == 200
    page = response.json()
    assert page["events"] == []
    assert page["status"] == "failed"
    assert page["continuation"]["state"] == "available"
    assert page["continuation"]["previous_run_id"] == first_id
    assert backend.get(f"/runs/{first_id}/events").json()["continuation"]["state"] == "superseded"

    (run_workspace / "state" / "reject-resume").unlink()
    retried = backend.post(f"/runs/{failed_id}/continue", json={"prompt": "recall the marker"})
    assert retried.status_code == 200
    assert "niobium" in retried.text
    assert backend.get(f"/runs/{retried.headers['X-Run-ID']}/events").json()["continuation"]["state"] == "available"


@pytest.mark.usefixtures("executor_socket")
def test_a_continuation_rejected_by_a_busy_executor_can_be_retried(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = live_http
    first_id = backend.post("/runs", json=prompt_body("save marker: niobium")).headers["X-Run-ID"]
    # Both prompts can pass the backend check at once; the executor then rejects the second.
    monkeypatch.setattr(CaptureFiles, "any_active", lambda root: False)
    with backend.stream("POST", "/runs", json=prompt_body(LITERAL_PROMPT)) as other:
        frames = other.iter_lines()
        next_frame(frames)
        busy = backend.post(f"/runs/{first_id}/continue", json={"prompt": "recall the marker"})
        (run_workspace / "state" / "release").touch()
        list(frames)
    assert busy.status_code == 200
    busy_id = busy.headers["X-Run-ID"]
    assert backend.get(f"/runs/{busy_id}/events").json()["continuation"]["state"] == "available"

    retried = backend.post(f"/runs/{busy_id}/continue", json={"prompt": "recall the marker"})
    assert retried.status_code == 200
    assert "niobium" in retried.text


@pytest.mark.usefixtures("executor_socket")
def test_prompts_are_refused_without_being_saved_while_a_run_is_active(
    live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    first_id = backend.post("/runs", json=prompt_body("save marker: niobium")).headers["X-Run-ID"]
    captures = run_workspace / "captures"
    with backend.stream("POST", "/runs", json=prompt_body(LITERAL_PROMPT)) as other:
        frames = other.iter_lines()
        next_frame(frames)
        saved = sorted(captures.iterdir())
        responses = [
            backend.post("/runs", json=prompt_body("independent conversation")),
            backend.post(f"/runs/{first_id}/continue", json={"prompt": "recall the marker"}),
        ]
        assert [(r.status_code, r.json()["detail"]) for r in responses] == [(409, "executor_busy")] * 2
        assert sorted(captures.iterdir()) == saved
        (run_workspace / "state" / "release").touch()
        list(frames)

    assert backend.get(f"/runs/{first_id}/events").json()["continuation"]["state"] == "available"
    retried = backend.post(f"/runs/{first_id}/continue", json={"prompt": "recall the marker"})
    assert retried.status_code == 200
    assert "niobium" in retried.text


@pytest.mark.usefixtures("executor_socket")
def test_a_continuation_that_times_out_can_be_retried(
    live_http: tuple[httpx.Client, Event], run_workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = live_http
    first_id = backend.post("/runs", json=prompt_body("save marker: niobium")).headers["X-Run-ID"]
    monkeypatch.setattr(executor_client, "READ_TIMEOUT_SECONDS", 0.2)
    silent = backend.post(f"/runs/{first_id}/continue", json={"prompt": "stay silent"})
    (run_workspace / "state" / "release").touch()
    monkeypatch.setattr(executor_client, "READ_TIMEOUT_SECONDS", 120.0)
    assert silent.status_code == 200
    silent_id = silent.headers["X-Run-ID"]
    assert backend.get(f"/runs/{silent_id}/events").json()["continuation"]["state"] == "available"

    deadline = monotonic() + 5
    while True:
        retried = backend.post(f"/runs/{silent_id}/continue", json={"prompt": "recall the marker"})
        if "niobium" in retried.text or monotonic() >= deadline:
            break
        silent_id = retried.headers["X-Run-ID"]
        sleep(0.05)
    assert retried.status_code == 200
    assert "niobium" in retried.text
