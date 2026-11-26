"""Describe the saved runs that conversation use cases read."""

from collections.abc import Generator
from datetime import datetime
from typing import Protocol

from src.domain.turn_summary import TurnSummary

from .run_continuation import RecordedResult, SavedConversationRun


class ConversationResult(RecordedResult, Protocol):
    assistant_markdown: str | None
    last_turn: str | None
    finished_at: datetime | None
    capture_failed: bool
    summary: TurnSummary | None


class ConversationRun(SavedConversationRun, Protocol):
    @property
    def run_id(self) -> str: ...
    def read_result(self) -> ConversationResult | None: ...
    def has_trace(self) -> bool: ...
    def read_trace_lines(self, *, finished: bool) -> Generator[str]: ...


class ConversationRepository[Run: ConversationRun](Protocol):
    def find(self, run_id: str) -> Run: ...
    def roots(self) -> tuple[list[Run], int]:
        """Return the readable first runs and the number of unreadable ones."""
        ...
    def lineage(self, root: Run) -> list[Run]: ...
    def root_for(self, run: Run) -> Run: ...
