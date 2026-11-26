import asyncio
from asyncio import CancelledError

import pytest

from src.executor.codex.app_server import AppServerTurn
from src.shared.observed_app import Delivery, ObserveHTTP


class BrokenTurn(AppServerTurn):
    def __init__(self) -> None:
        pass

    async def live_events(self, capture, pending):
        yield {"type": "live"}
        raise ValueError("broken stream")

    def interrupted_events(self, capture):
        yield {"type": "held"}


def test_a_broken_capture_yields_what_was_held_before_failing() -> None:
    events: list[object] = []

    async def collect() -> None:
        async for event in BrokenTurn().capture_events(None, []):  # type: ignore[arg-type]
            events.append(event)

    with pytest.raises(ValueError, match="broken stream"):
        asyncio.run(collect())
    assert events == [{"type": "live"}, {"type": "held"}]


def test_a_cancelled_request_is_recorded_as_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "src.shared.observed_app.finish_span",
        lambda span, outcome, *, error_code=None: outcomes.append((outcome, error_code)),
    )

    async def cancelled(scope, receive, send) -> None:
        raise CancelledError

    async def unused(*args) -> None:
        return None

    observer = ObserveHTTP(cancelled, state=None, routes={}, private=False)  # type: ignore[arg-type]
    with pytest.raises(CancelledError):
        asyncio.run(observer._observe(None, "run", {}, unused, Delivery(unused)))  # type: ignore[arg-type]
    assert outcomes == [("disconnected", "client_disconnected")]
