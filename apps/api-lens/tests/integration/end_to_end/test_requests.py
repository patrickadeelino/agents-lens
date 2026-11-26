"""Refuse a prompt that cannot be accepted."""

from pathlib import Path
from threading import Event

import httpx
import pytest

from src.backend.infrastructure.executor_client import ExecutorClient
from tests.integration.end_to_end.support import prompt_body


@pytest.mark.parametrize("body", [{}, {"prompt": 42}, {"prompt": "  \n "}, {"prompt": "read", "workspace": "/"}])
def test_run_rejects_invalid_request_before_capture(
    body: dict[str, object],
    live_http: tuple[httpx.Client, Event],
    run_workspace: Path,
) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json=body)

    assert response.status_code == 422
    assert not (run_workspace / "captures").exists()


def test_run_requires_a_host_executor(live_http: tuple[httpx.Client, Event], run_workspace: Path) -> None:
    backend, _ = live_http
    response = backend.post("/runs", json=prompt_body("Read the example."))

    assert response.status_code == 503
    assert response.json() == {"detail": "Executor socket is not configured."}
    assert not (run_workspace / "captures").exists()


def test_run_rejects_unavailable_capture_directory(
    monkeypatch: pytest.MonkeyPatch, live_http: tuple[httpx.Client, Event], run_workspace: Path,
) -> None:
    backend, _ = live_http
    monkeypatch.setenv("AGENT_LENS_EXECUTOR_SOCKET", str(run_workspace / "unused.sock"))
    monkeypatch.setattr(ExecutorClient, "selected_folder", lambda *_: str(run_workspace / "example"))
    monkeypatch.setattr(ExecutorClient, "check_agent", lambda *_: None)
    (run_workspace / "captures").touch()
    response = backend.post("/runs", json=prompt_body("Read the example."))

    assert response.status_code == 503
    assert response.json() == {"detail": "Capture directory is unavailable."}
