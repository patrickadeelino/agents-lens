from asyncio import run

import pytest

from src.application import replay
from src.domain.trace_events import TimedEvent, TraceEvent


def test_replay_uses_recorded_intervals_without_accumulating_consumer_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ns = 4_000_000_000
    waits: list[float] = []
    records = tuple(
        TimedEvent(elapsed_ns=offset, event=TraceEvent(type="event"))
        for offset in (25_000_000, 75_000_000, 125_000_000)
    )

    async def sleep(seconds: float) -> None:
        nonlocal now_ns
        waits.append(seconds)
        now_ns += round(seconds * 1_000_000_000)

    async def collect() -> list[TimedEvent]:
        nonlocal now_ns
        result = []
        async for record in replay.TimedReplay(records).stream():
            result.append(record)
            now_ns += 20_000_000
        return result

    monkeypatch.setattr(replay, "monotonic_ns", lambda: now_ns)
    monkeypatch.setattr(replay, "sleep", sleep)

    assert run(collect()) == list(records)
    assert waits == pytest.approx([0, 0.03, 0.03])
