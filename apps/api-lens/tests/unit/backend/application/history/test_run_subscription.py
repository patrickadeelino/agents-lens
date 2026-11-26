import asyncio
from contextlib import contextmanager

from src.backend.application.history.run_subscription import (
    SubscribeToRun,
    SubscriptionEnded,
)


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
