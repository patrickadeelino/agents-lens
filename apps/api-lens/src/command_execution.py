"""Derived state for a command observed in a Codex trace."""

from dataclasses import dataclass


@dataclass
class CommandExecutionState:
    item_id: str
    command: str
    status: str
    aggregated_output: str = ""
    exit_code: int | None = None
    started_offset_ns: int | None = None
    completed_offset_ns: int | None = None

    @property
    def observed_duration_ns(self) -> int | None:
        """Return the interval between the observed start and completion."""
        if self.started_offset_ns is None or self.completed_offset_ns is None:
            return None
        return self.completed_offset_ns - self.started_offset_ns
