"""Web pages from other origins must not reach the local API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.api import dependencies
from src.backend.main import app


@pytest.fixture(autouse=True)
def captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "captures"
    monkeypatch.setattr(dependencies, "CAPTURE_ROOT", root)
    return root


@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8000"])
def test_local_host_names_are_accepted(host: str) -> None:
    with TestClient(app, base_url=f"http://{host}") as client:
        assert client.get("/threads").status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "backend:8000"])
def test_other_host_names_are_rejected(host: str) -> None:
    with TestClient(app, base_url=f"http://{host}") as client:
        assert client.get("/threads").status_code == 400


# A cross-origin page can send these without a CORS preflight.
@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_a_prompt_that_skips_preflight_is_rejected(content_type: str | None, captures: Path) -> None:
    headers = {"Origin": "http://evil.example"} | ({"Content-Type": content_type} if content_type else {})
    body = b'{"prompt":"hi","workspace_run_id":"codex-prompt-x"}'
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/runs", content=body, headers=headers).status_code == 422
    assert not captures.exists()
