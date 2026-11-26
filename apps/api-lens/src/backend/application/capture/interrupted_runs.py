"""Close the runs that a previous API process left without a result."""

from collections.abc import Iterable
from typing import Protocol

from src.shared.observability import event_log


class UnfinishedRun(Protocol):
    def read_session(self) -> object | None: ...
    def read_result(self) -> object | None: ...
    def ensure_trace(self) -> None: ...
    def write_result(self, exit_code: int | None) -> None: ...


def close_interrupted_runs(runs: Iterable[UnfinishedRun]) -> None:
    """Record an unconfirmed result for every saved run that never received one.

    Only safe while no run can be active: at startup, with a single API process.
    """
    for run in runs:
        try:
            close_if_interrupted(run)
        except (OSError, ValueError):
            event_log("run.recovery.failed", "history", "failed", error_code="capture_unreadable")


def close_if_interrupted(run: UnfinishedRun) -> None:
    if run.read_session() is None or run.read_result() is not None:
        return
    run.ensure_trace()
    run.write_result(None)
    event_log("run.recovery.closed", "history", "unconfirmed", error_code="api_restarted")
