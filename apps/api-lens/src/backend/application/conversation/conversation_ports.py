"""Describe the saved runs that conversation use cases read."""

from collections.abc import Generator
from datetime import datetime
from typing import Protocol

from src.backend.application.conversation.run_continuation import (
    RecordedResult,
    SavedConversationRun,
)
from src.backend.domain.turn.turn_summary import TurnSummary


class ConversationResult(RecordedResult, Protocol):
    assistant_markdown: str | None
    last_turn: str | None
    finished_at: datetime | None
    summary: TurnSummary | None


class ConversationRun(SavedConversationRun, Protocol):
    def read_result(self) -> ConversationResult | None: ...
    def has_trace(self) -> bool: ...
    def read_trace_lines(self, *, finished: bool) -> Generator[str]: ...


class ConversationRepository[Run: ConversationRun](Protocol):
    def find(self, run_id: str) -> Run: ...
    def roots(self) -> tuple[list[Run], int]:
        """Return the readable first runs and the number of unreadable ones."""
        ...
