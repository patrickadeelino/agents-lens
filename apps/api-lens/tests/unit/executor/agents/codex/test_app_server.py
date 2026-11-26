import asyncio

import pytest

from src.executor.agents.codex.app_server import AppServerError, AppServerTurn


class ScriptedTurn(AppServerTurn):
    def __init__(self, *messages: dict[str, object]) -> None:
        self.messages = list(messages)
        self.exit_code = None

    async def receive(self):
        if not self.messages:
            await asyncio.sleep(5)
        return self.messages.pop(0)


class BrokenTurn(AppServerTurn):
    def __init__(self) -> None:
        pass

    async def live_events(self, capture, pending):
        yield {"type": "live"}
        raise ValueError("broken stream")

    def interrupted_events(self, capture):
        yield {"type": "held"}


def test_a_reply_holds_the_notifications_that_arrive_before_it() -> None:
    note: dict[str, object] = {"method": "item/started"}
    turn = ScriptedTurn(note, {"id": 9, "result": {}}, {"id": 4, "method": "ask"}, {"id": 4, "result": {"ok": True}})
    pending: list[dict[str, object]] = []
    assert asyncio.run(turn.reply(4, pending)) == {"ok": True}
    assert pending == [note]


def test_a_reply_refuses_to_hold_more_than_128_notifications() -> None:
    notes: list[dict[str, object]] = [{"method": "item/started"} for _ in range(129)]
    turn = ScriptedTurn(*notes)
    with pytest.raises(AppServerError):
        asyncio.run(turn.reply(4, []))


def test_an_expired_deadline_releases_what_the_capture_was_holding() -> None:
    turn = ScriptedTurn()

    class Capture:
        deadline: float | None = None

        def flush(self):
            self.deadline = None
            return ["first", "second"]

    async def collect() -> list[object]:
        capture = Capture()
        capture.deadline = asyncio.get_running_loop().time() + 0.01
        released: list[object] = []
        async for note in turn.notifications(capture, []):  # type: ignore[arg-type]
            released.append(note)
            turn.exit_code = 0 if len(released) == 2 else None
        return released

    assert asyncio.run(collect()) == ["first", "second"]


def test_a_broken_capture_yields_what_was_held_before_failing() -> None:
    events: list[object] = []

    async def collect() -> None:
        async for event in BrokenTurn().capture_events(None, []):  # type: ignore[arg-type]
            events.append(event)

    with pytest.raises(ValueError, match="broken stream"):
        asyncio.run(collect())
    assert events == [{"type": "live"}, {"type": "held"}]
