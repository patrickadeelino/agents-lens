"""Replay turn and command lifecycle events from a recorded Codex CLI run."""

from collections.abc import Iterable
from typing import TextIO

from src.domain.command_execution import CommandExecutions, CommandExecutionState
from src.domain.trace_events import TraceEvent
from src.domain.trace_reader import read_trace


class TerminalReplay:
    def __init__(self, output: TextIO) -> None:
        self._output = output

    def _write_line(self, text: str) -> None:
        """Escape terminal control characters that may occur in captured text."""
        print(text if text.isprintable() or not text else repr(text), file=self._output)

    def replay(self, lines: Iterable[str]) -> dict[str, CommandExecutionState]:
        """Display the observed command transitions and return the items by ID."""
        commands = CommandExecutions()

        for record in read_trace(lines):
            if record.event.type in {"turn.started", "turn.completed", "turn.failed"}:
                self._write_turn(record.event)
            elif (command := commands.observe(record)) is not None:
                self._write_command_status(command)
                if record.event.type == "item.completed":
                    self._write_completion(command)

        self._finish_pending(commands.by_id.values())
        return commands.by_id

    def _write_turn(self, event: TraceEvent) -> None:
        self._write_line(f"[{event.type}]")
        if event.type == "turn.failed":
            event_data = event.model_extra or {}
            self._write_line(f'  {event_data["error"]["message"]}')

    def _write_command_status(self, command: CommandExecutionState) -> None:
        self._write_line(f"[{command.status}] {command.item_id} {command.command}")

    def _write_completion(self, command: CommandExecutionState) -> None:
        if command.exit_code is not None:
            self._write_line(f"  exit code: {command.exit_code}")
        duration_ns = command.observed_duration_ns
        if duration_ns is not None:
            self._write_line(f"  observed duration: {duration_ns / 1_000_000:.3f} ms")
        for output_line in command.aggregated_output.splitlines():
            self._write_line(f"  {output_line}")

    def _finish_pending(self, commands: Iterable[CommandExecutionState]) -> None:
        for command in commands:
            if command.status == "in_progress":
                command.status = "result_unconfirmed"
                self._write_command_status(command)
