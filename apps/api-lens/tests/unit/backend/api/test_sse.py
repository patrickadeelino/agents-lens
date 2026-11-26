from asyncio import run
from collections.abc import AsyncGenerator

import pytest
from starlette.types import Message

from src.backend.api.sse import RunSubscriptionResponse
from src.backend.application.history.run_subscription import SubscriptionMessage


def test_subscription_closes_its_source_when_sending_a_keepalive_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    detached = False
    monkeypatch.setattr(RunSubscriptionResponse, "_heartbeat_seconds", 0.0)

    async def messages() -> AsyncGenerator[SubscriptionMessage]:
        nonlocal detached
        try:
            yield None
        finally:
            detached = True

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            assert message["body"] == b": keep-alive\n\n"
            raise ConnectionResetError

    with pytest.raises(ConnectionResetError):
        run(RunSubscriptionResponse(messages()).stream_response(send))

    assert detached
