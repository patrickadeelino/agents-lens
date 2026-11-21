"""Replay turn and command lifecycle events from a recorded Codex CLI run."""

from collections.abc import Iterable
from typing import TextIO

from .command_execution import CommandExecutionState
from .trace_events import TraceEvent
from .trace_reader import TraceRecord, read_trace


class TerminalReplay:
    def __init__(self, output: TextIO) -> None:
        self._output = output

    def _write_line(self, text: str) -> None:
        """Escape terminal control characters that may occur in captured text."""
        print(text if text.isprintable() or not text else repr(text), file=self._output)

    def replay(self, lines: Iterable[str]) -> dict[str, CommandExecutionState]:
        """Display the observed command transitions and return the items by ID."""
        commands: dict[str, CommandExecutionState] = {}

        for record in read_trace(lines):
            if record.event.type in {"turn.started", "turn.completed", "turn.failed"}:
                self._write_turn(record.event)
            elif record.event.type in {"item.started", "item.completed"}:
                self._apply_command_event(record, commands)

        self._finish_pending(commands.values())
        return commands

    def _write_turn(self, event: TraceEvent) -> None:
        self._write_line(f"[{event.type}]")
        if event.type == "turn.failed":
            event_data = event.model_extra or {}
            self._write_line(f'  {event_data["error"]["message"]}')

    def _apply_command_event(
        self, record: TraceRecord, commands: dict[str, CommandExecutionState]
    ) -> None:
        event_data = record.event.model_extra or {}
        item = event_data["item"]
        if item["type"] != "command_execution":
            return

        item_id = item["id"]
        command = commands.get(item_id)
        if command is None:
            command = CommandExecutionState(
                item_id=item_id, command=item["command"], status=item["status"]
            )
            commands[item_id] = command

        command.command = item.get("command", command.command)
        command.status = item.get("status", command.status)
        command.aggregated_output = item.get("aggregated_output", command.aggregated_output)
        command.exit_code = item.get("exit_code", command.exit_code)

        if record.event.type == "item.started" and command.started_offset_ns is None:
            command.started_offset_ns = record.elapsed_ns
        self._write_command_status(command)

        if record.event.type == "item.completed":
            command.completed_offset_ns = record.elapsed_ns
            self._write_completion(command)

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
