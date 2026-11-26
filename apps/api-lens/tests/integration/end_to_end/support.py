"""Share the servers and readers used by the end-to-end tests."""

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from uuid import UUID

import pytest
import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

LITERAL_PROMPT = '  --help\nLeia o README "juniper" ☕.\n'


def open_listener(unix_path: Path | None) -> tuple[socket.socket, str]:
    if unix_path is None:
        listener = socket.create_server(("127.0.0.1", 0))
        return listener, f"http://127.0.0.1:{listener.getsockname()[1]}"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(unix_path))
    listener.listen()
    return listener, "http://executor-host"


def wait_until_started(server: uvicorn.Server) -> None:
    deadline = monotonic() + 5
    while not server.started:
        if monotonic() >= deadline:
            pytest.fail("Uvicorn did not start")
        sleep(0.01)


@contextmanager
def running_server(
    application: ASGIApp, request_finished: Event, *, unix_path: Path | None = None,
) -> Iterator[str]:
    async def observe(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await application(scope, receive, send)
        finally:
            request_finished.set()

    listener, url = open_listener(unix_path)
    with listener:
        server = uvicorn.Server(uvicorn.Config(observe, lifespan="off", log_level="warning"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            wait_until_started(server)
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=6)
            assert not thread.is_alive(), "Uvicorn did not stop"


WORKSPACE_ID = UUID("17e080e0-d42b-49d2-bbbc-18bd7cdcaa11")


def prompt_body(prompt: str) -> dict[str, str]:
    return {"prompt": prompt, "workspace_id": str(WORKSPACE_ID)}


def next_frame(lines: Iterator[str]) -> tuple[str, dict[str, object]]:
    event_line = next(lines)
    data_line = next(lines)
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    assert next(lines) == ""
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return event_line.removeprefix("event: "), payload


def capture_directory(workspace: Path) -> Path:
    (directory,) = (workspace / "captures").glob("codex-prompt-*")
    return directory
