"""Replay recorded events at their observed arrival intervals."""

from asyncio import sleep
from collections.abc import AsyncIterator
from itertools import pairwise
from time import monotonic_ns

from src.domain.trace_events import TimedEvent


class InvalidReplayOffsets(ValueError):
    """The trace cannot be replayed in arrival order."""


class TimedReplay:
    def __init__(self, records: tuple[TimedEvent, ...]) -> None:
        if not records or any(
            current.elapsed_ns > following.elapsed_ns
            for current, following in pairwise(records)
        ):
            raise InvalidReplayOffsets("Replay trace has invalid arrival offsets.")
        self._records = records

    async def stream(self) -> AsyncIterator[TimedEvent]:
        """Send the first event immediately and preserve recorded intervals."""
        first_offset_ns = self._records[0].elapsed_ns
        started_at_ns = monotonic_ns()
        for record in self._records:
            target_ns = started_at_ns + record.elapsed_ns - first_offset_ns
            remaining_ns = max(0, target_ns - monotonic_ns())
            await sleep(remaining_ns / 1_000_000_000)
            yield record
