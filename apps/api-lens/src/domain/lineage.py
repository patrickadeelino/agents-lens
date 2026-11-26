"""Walk the chain that links each turn of a conversation to the next."""

from collections.abc import Callable, Iterator
from typing import Protocol, Self


class LinkedRun(Protocol):
    @property
    def run_id(self) -> str: ...
    def previous_run_id(self) -> str | None: ...
    def previous(self) -> Self | None:
        """Load the run named by previous_run_id."""
        ...
    def successor(self) -> Self | None: ...


class InvalidLineage(ValueError):
    """The saved links do not form a single chain."""

    def __init__(self) -> None:
        super().__init__("Invalid conversation lineage.")


def descendants[Run: LinkedRun](start: Run) -> Iterator[Run]:
    """Yield the run and then each later turn, ending at the latest one."""
    return _walk(start, lambda run: run.successor(), lambda run, later: later.previous_run_id() == run.run_id)


def ancestors[Run: LinkedRun](start: Run) -> Iterator[Run]:
    """Yield the run and then each earlier turn, ending at the first one."""

    def points_back(run: Run, earlier: Run) -> bool:
        later = earlier.successor()
        return later is not None and later.run_id == run.run_id

    return _walk(start, lambda run: run.previous(), points_back)


def _walk[Run: LinkedRun](
    start: Run, step: Callable[[Run], Run | None], linked: Callable[[Run, Run], bool],
) -> Iterator[Run]:
    # A link is followed only when both runs agree on it.
    visited = {start.run_id}
    run = start
    yield run
    while (neighbour := step(run)) is not None:
        if neighbour.run_id in visited or not linked(run, neighbour):
            raise InvalidLineage
        visited.add(neighbour.run_id)
        run = neighbour
        yield run
