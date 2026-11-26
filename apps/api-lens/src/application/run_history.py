"""Build a page of saved events and the command state at the observed end of the trace."""

from collections import deque
from collections.abc import Callable, Generator, Iterable
from contextlib import closing
from dataclasses import dataclass
from typing import Literal, Protocol, Self

from src.domain.command_execution import CommandExecutions, CommandExecutionState
from src.domain.trace_events import TimedEvent
from src.domain.trace_reader import read_timed_trace
from src.domain.turn_outcome import TurnStatus, turn_boundary, turn_status

from .run_continuation import (
    ContinuationInfo,
    RecordedResult,
    SavedConversationRun,
    describe_continuation,
)

type RunReadErrorKind = Literal["run_not_found", "trace_not_found", "capture_starting", "capture_unavailable"]


class RunReadError(Exception):
    def __init__(self, kind: RunReadErrorKind) -> None:
        super().__init__(kind)
        self.kind = kind


class RunHistorySource(SavedConversationRun, Protocol):
    def read_result(self) -> RecordedResult | None: ...

    def read_trace_lines(self, *, finished: bool) -> Generator[str]: ...

@dataclass(frozen=True)
class HistoryEvent:
    event_id: int
    record: TimedEvent


@dataclass(frozen=True)
class CommandSummary:
    command: str
    status: str
    exit_code: int | None
    observed_duration_ns: int | None

    @classmethod
    def from_state(cls, state: CommandExecutionState) -> Self:
        return cls(state.command, state.status, state.exit_code, state.observed_duration_ns)


@dataclass(frozen=True)
class RunHistoryPage:
    run_id: str
    events: tuple[HistoryEvent, ...]
    commands: dict[str, CommandSummary]
    latest_event_id: int | None
    next_before: int | None
    status: TurnStatus
    exit_code: int | None
    last_turn: str | None
    continuation: ContinuationInfo


class GetRunHistory:
    """Read and project the history of an existing capture."""

    def __init__(self, find_capture: Callable[[str], RunHistorySource]) -> None:
        self._find_capture = find_capture

    def execute(self, run_id: str, *, limit: int, before: int | None) -> RunHistoryPage:
        try:
            capture = self._find_capture(run_id)
        except FileNotFoundError as error:
            raise RunReadError("run_not_found") from error
        except OSError as error:
            raise RunReadError("capture_unavailable") from error

        try:
            # Read the result before the trace so it cannot describe a later point.
            result = capture.read_result()
            with closing(capture.read_trace_lines(finished=result is not None)) as lines:
                return self._build_page(
                    run_id, lines, limit=limit, before=before, exit_code=result.exit_code if result else None,
                    continuation=describe_continuation(run_id, capture, result),
                    capture_failed=result.capture_failed if result else False,
                )
        except FileNotFoundError as error:
            kind: RunReadErrorKind = "capture_starting" if capture.is_active() else "trace_not_found"
            raise RunReadError(kind) from error
        except (OSError, ValueError) as error:
            raise RunReadError("capture_unavailable") from error


    @classmethod
    def _build_page(
        cls, run_id: str, lines: Iterable[str], *, limit: int, before: int | None, exit_code: int | None,
        continuation: ContinuationInfo,
        capture_failed: bool = False,
    ) -> RunHistoryPage:
        page: deque[tuple[HistoryEvent, str | None]] = deque(maxlen=limit)
        commands = CommandExecutions(retain_output=False)
        latest_event_id: int | None = None
        last_turn: str | None = None
        previous_offset_ns = 0
        for event_id, record in enumerate(read_timed_trace(lines), start=1):
            if record.elapsed_ns < previous_offset_ns:
                raise ValueError("History trace has invalid arrival offsets.")
            previous_offset_ns = record.elapsed_ns
            latest_event_id = event_id
            last_turn = turn_boundary(record.event) or last_turn
            command = commands.observe(record)
            if before is None or event_id < before:
                page.append((HistoryEvent(event_id, record), command.item_id if command else None))

        events = tuple(entry for entry, _ in page)
        summaries = {item: CommandSummary.from_state(commands.by_id[item]) for _, item in page if item is not None}
        next_before = events[0].event_id if events and events[0].event_id > 1 else None
        return RunHistoryPage(
            run_id, events, summaries, latest_event_id, next_before,
            turn_status(last_turn, exit_code, capture_failed=capture_failed), exit_code, last_turn, continuation,
        )
