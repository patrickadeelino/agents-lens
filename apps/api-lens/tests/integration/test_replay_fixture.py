import io
from pathlib import Path

from scripts.terminal_replay import TerminalReplay


def test_real_capture_updates_the_same_command(project_root: Path) -> None:
    trace = project_root / "examples" / "traces" / "readme-run.sanitized.jsonl"
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
