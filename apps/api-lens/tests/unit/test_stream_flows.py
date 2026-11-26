import asyncio
from contextlib import contextmanager

import pytest

from src.backend.application.history.run_subscription import (
    SubscribeToRun,
    SubscriptionEnded,
)
from src.executor.codex.app_server import AppServerError, AppServerTurn


class Tail:
    def __init__(self, *messages: object) -> None:
        self.messages = list(messages)
        self.closed = False

    async def read(self):
        return self.messages.pop(0)


def source(tail: Tail):
    class Source:
        @contextmanager
        def open_tail(self):
            try:
                yield tail
            finally:
                tail.closed = True

    return lambda run_id: Source()


RECORD = '{"elapsed_ns":0,"event":{"type":"turn.started"}}\n'


def follow(tail: Tail, after: int) -> list[object]:
    async def collect() -> list[object]:
        return [message async for message in SubscribeToRun(source(tail)).execute("run", after=after)]

    return asyncio.run(collect())


def test_a_subscription_skips_seen_records_pauses_and_ends_with_the_result() -> None:
    class Result:
        exit_code = 0

    tail = Tail(RECORD, RECORD, None, Result())
    event, pause, end = follow(tail, after=1)
    assert (event.event_id, pause, end) == (2, None, SubscriptionEnded(0))  # type: ignore[attr-defined]
    assert tail.closed


def test_an_unreadable_record_ends_the_subscription_and_closes_the_tail() -> None:
    tail = Tail("not json\n")
    assert follow(tail, after=0) == [SubscriptionEnded(None, "capture_unavailable")]
    assert tail.closed


def test_closing_a_subscription_early_closes_the_tail() -> None:
    tail = Tail(RECORD, RECORD)

    async def read_one() -> None:
        messages = SubscribeToRun(source(tail)).execute("run", after=0)
        await anext(messages)
        await messages.aclose()
        assert tail.closed

    asyncio.run(read_one())


class ScriptedTurn(AppServerTurn):
    def __init__(self, *messages: dict[str, object]) -> None:
        self.messages = list(messages)
        self.exit_code = None

    async def receive(self):
        if not self.messages:
            await asyncio.sleep(5)
        return self.messages.pop(0)


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
