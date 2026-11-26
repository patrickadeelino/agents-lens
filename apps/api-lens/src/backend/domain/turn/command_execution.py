"""Derived state for a command observed in a Codex trace."""

from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationError

from src.shared.trace_events import TimedEvent, TraceEvent


class CommandItem(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    id: str
    command: str | None = None
    status: str | None = None
    aggregated_output: str | None = None
    exit_code: int | None = None

    @classmethod
    def from_event(cls, event: TraceEvent) -> Self | None:
        if event.type not in {"item.started", "item.completed"}:
            return None
        item = (event.model_extra or {}).get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            return None
        try:
            return cls.model_validate(item)
        except ValidationError:
            return None


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
        duration_ns = self.completed_offset_ns - self.started_offset_ns
        return duration_ns if duration_ns >= 0 else None


class CommandExecutions:
    """Project observed command events into the latest state of each item."""

    def __init__(self, *, retain_output: bool = True) -> None:
        self.by_id: dict[str, CommandExecutionState] = {}
        self._retain_output = retain_output

    def observe(self, record: TimedEvent) -> CommandExecutionState | None:
        item = CommandItem.from_event(record.event)
        if item is None:
            return None

        command = self.by_id.setdefault(item.id, CommandExecutionState(item.id, "", "result_unconfirmed"))
        command.command = item.command if item.command is not None else command.command
        command.status = item.status if item.status is not None else command.status
        if self._retain_output and item.aggregated_output is not None:
            command.aggregated_output = item.aggregated_output
        if "exit_code" in item.model_fields_set:
            command.exit_code = item.exit_code
        if record.event.type == "item.started" and command.started_offset_ns is None:
            command.started_offset_ns = record.elapsed_ns
        if record.event.type == "item.completed":
            command.completed_offset_ns = record.elapsed_ns
        return command
