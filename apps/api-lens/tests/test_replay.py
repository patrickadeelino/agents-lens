import io
import json
from pathlib import Path

import pytest

from src.replay import TerminalReplay


def test_real_capture_updates_the_same_command() -> None:
    project_dir = Path(__file__).resolve().parents[3]
    trace = project_dir / "examples" / "traces" / "readme-run.sanitized.jsonl"
    output = io.StringIO()

    with trace.open(encoding="utf-8") as lines:
        commands = TerminalReplay(output).replay(lines)

    assert list(commands) == ["item_5"]
    assert commands["item_5"].status == "completed"
    assert commands["item_5"].exit_code == 0
    command = "zsh -lc 'cat <global-instructions>/RTK.md README.md'"
    assert output.getvalue().splitlines() == [
        "[turn.started]",
        f"[in_progress] item_5 {command}",
        f"[completed] item_5 {command}",
        "  exit code: 0",
        "  [redacted: global instruction contents]",
        "  The example service is named juniper.",
        "[turn.completed]",
    ]


def test_commands_can_complete_in_reverse_order() -> None:
    first = {
        "id": "item_5",
        "type": "command_execution",
        "command": "cat first.txt",
        "status": "in_progress",
        "exit_code": None,
        "aggregated_output": "",
    }
    second = {**first, "id": "item_6", "command": "cat second.txt"}
    completed = {"status": "completed", "exit_code": 0}
    events = [
        {"type": "item.started", "item": first},
        {"type": "item.started", "item": second},
        {
            "type": "item.completed",
            "item": {
                "id": "item_6",
                "type": "command_execution",
                **completed,
                "aggregated_output": "second result\n",
            },
        },
        {
            "type": "item.completed",
            "item": {**first, **completed, "aggregated_output": "first result\n"},
        },
    ]
    output = io.StringIO()

    commands = TerminalReplay(output).replay(json.dumps(event) for event in events)

    assert commands["item_5"].aggregated_output == "first result\n"
    assert commands["item_6"].aggregated_output == "second result\n"
    assert output.getvalue().splitlines() == [
        "[in_progress] item_5 cat first.txt",
        "[in_progress] item_6 cat second.txt",
        "[completed] item_6 cat second.txt",
        "  exit code: 0",
        "  second result",
        "[completed] item_5 cat first.txt",
        "  exit code: 0",
        "  first result",
    ]


def test_pending_command_has_unconfirmed_result_at_end_of_capture() -> None:
    started = {
        "type": "item.started",
        "item": {
            "id": "item_5",
            "type": "command_execution",
            "command": "cat README.md",
            "status": "in_progress",
            "exit_code": None,
            "aggregated_output": "",
        },
    }
    output = io.StringIO()

    commands = TerminalReplay(output).replay([json.dumps(started)])

    assert commands["item_5"].status == "result_unconfirmed"
    assert commands["item_5"].exit_code is None
    assert output.getvalue().splitlines() == [
        "[in_progress] item_5 cat README.md",
        "[result_unconfirmed] item_5 cat README.md",
    ]


def test_failed_turn_reports_the_error() -> None:
    events = [
        {"type": "turn.started"},
        {"type": "turn.failed", "error": {"message": "The model is unavailable."}},
    ]
    output = io.StringIO()

    commands = TerminalReplay(output).replay(json.dumps(event) for event in events)

    assert commands == {}
    assert output.getvalue().splitlines() == [
        "[turn.started]",
        "[turn.failed]",
        "  The model is unavailable.",
    ]


def test_completed_turn_keeps_the_failed_command_visible() -> None:
    failed = {
        "id": "item_5",
        "type": "command_execution",
        "command": "cat missing.txt",
        "status": "failed",
        "exit_code": 1,
        "aggregated_output": "cat: missing.txt: No such file or directory\n",
    }
    recovered = {
        "id": "item_6",
        "type": "command_execution",
        "command": "cat README.md",
        "status": "completed",
        "exit_code": 0,
        "aggregated_output": "The example service is named juniper.\n",
    }
    pending = {"status": "in_progress", "exit_code": None, "aggregated_output": ""}
    events = [
        {"type": "turn.started"},
        {"type": "item.started", "item": {**failed, **pending}},
        {"type": "item.completed", "item": failed},
        {"type": "item.started", "item": {**recovered, **pending}},
        {"type": "item.completed", "item": recovered},
        {"type": "turn.completed"},
    ]
    output = io.StringIO()

    commands = TerminalReplay(output).replay(json.dumps(event) for event in events)

    assert commands["item_5"].status == "failed"
    assert commands["item_6"].status == "completed"
    assert output.getvalue().splitlines() == [
        "[turn.started]",
        "[in_progress] item_5 cat missing.txt",
        "[failed] item_5 cat missing.txt",
        "  exit code: 1",
        "  cat: missing.txt: No such file or directory",
        "[in_progress] item_6 cat README.md",
        "[completed] item_6 cat README.md",
        "  exit code: 0",
        "  The example service is named juniper.",
        "[turn.completed]",
    ]


@pytest.mark.parametrize(
    ("start_offset_ns", "expected_duration_ns", "expected_duration_line"),
    [
        (25_000_000, 1_000_000_000, "  observed duration: 1000.000 ms"),
        (0, 1_025_000_000, "  observed duration: 1025.000 ms"),
        (None, None, None),
    ],
    ids=["timed-start", "zero-offset-start", "untimed-start"],
)
def test_replay_uses_both_arrival_offsets_for_command_duration(
    start_offset_ns: int | None, expected_duration_ns: int | None, expected_duration_line: str | None
) -> None:
    item = {
        "id": "item_5",
        "type": "command_execution",
        "command": "cat README.md",
        "status": "in_progress",
        "exit_code": None,
        "aggregated_output": "",
    }
    started = {"type": "item.started", "item": item}
    completed = {
        "type": "item.completed",
        "item": {**item, "status": "completed", "exit_code": 0, "aggregated_output": "juniper\n"},
    }
    records = [
        started if start_offset_ns is None else {"elapsed_ns": start_offset_ns, "event": started},
        {"elapsed_ns": 1_025_000_000, "event": completed},
    ]
    output = io.StringIO()

    commands = TerminalReplay(output).replay(json.dumps(record) for record in records)

    assert commands["item_5"].observed_duration_ns == expected_duration_ns

    expected = [
        "[in_progress] item_5 cat README.md",
        "[completed] item_5 cat README.md",
        "  exit code: 0",
    ]
    if expected_duration_line is not None:
        expected.append(expected_duration_line)
    assert output.getvalue().splitlines() == [*expected, "  juniper"]


def test_timed_completion_without_start_keeps_result_and_unknown_duration() -> None:
    record = {
        "elapsed_ns": 50_000_000,
        "event": {
            "type": "item.completed",
            "item": {
                "id": "item_5",
                "type": "command_execution",
                "command": "cat README.md",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "juniper\n",
            },
        },
    }
    output = io.StringIO()

    command = TerminalReplay(output).replay([json.dumps(record)])["item_5"]

    assert command.status == "completed"
    assert command.exit_code == 0
    assert command.started_offset_ns is None
    assert command.completed_offset_ns == 50_000_000
    assert command.observed_duration_ns is None
    assert output.getvalue().splitlines() == [
        "[completed] item_5 cat README.md",
        "  exit code: 0",
        "  juniper",
    ]
