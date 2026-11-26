import asyncio
from asyncio import CancelledError

import pytest

from src.shared.observed_app import Delivery, ObserveHTTP


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
