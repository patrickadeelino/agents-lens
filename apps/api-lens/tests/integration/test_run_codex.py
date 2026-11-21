import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts import run_codex
from src.application.capture import EventCapture
from src.infrastructure.codex_runner import CodexRunner


def test_capture_writes_events_before_process_exit_and_records_diagnostics(capture_root: Path) -> None:
    producer = """
import json
import sys
import time
from pathlib import Path

trace = Path(sys.argv[1])
assert Path.cwd() == Path(sys.argv[2])
print(json.dumps({"type": "turn.started"}), flush=True)
print("simulated diagnostic", file=sys.stderr, flush=True)
deadline = time.monotonic() + 3
while not trace.read_bytes().endswith(b"\\n"):
    if time.monotonic() >= deadline:
        raise SystemExit("First event was not captured while the process was running")
    time.sleep(0.01)
print(json.dumps({"type": "turn.failed", "error": {"message": "simulated failure"}}), flush=True)
raise SystemExit(7)
"""

    with TemporaryDirectory(prefix="test-", dir=capture_root) as directory:
        capture_dir = Path(directory)
        workspace = capture_dir / "workspace"
        workspace.mkdir()
        trace = capture_dir / "events.timed.jsonl"

        runner = CodexRunner(EventCapture())
        exit_code = runner.capture_command(
            [sys.executable, "-B", "-c", producer, str(trace), str(workspace)],
            cwd=workspace,
            capture_dir=capture_dir,
        )

        records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert [record["event"]["type"] for record in records] == ["turn.started", "turn.failed"]
        assert 0 <= records[0]["elapsed_ns"] <= records[1]["elapsed_ns"]
        assert (capture_dir / "stderr.log").read_text(encoding="utf-8") == "simulated diagnostic\n"
        assert json.loads((capture_dir / "result.json").read_text(encoding="utf-8")) == {"exit_code": 7}
        assert exit_code == 7


def test_run_codex_cli_preserves_prompt_sandbox_and_exit_code(
    capture_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = '--help\nRead "juniper".'
    with TemporaryDirectory(prefix="test-run-cli-", dir=capture_root) as directory:
        project = Path(directory)
        workspace = project / "workspace"
        workspace.mkdir()
        executable = project / "fake-codex"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "assert sys.argv[1:-1] == ['exec', '--json', '--sandbox', 'read-only',"
            " '--skip-git-repo-check', '--model', 'test-model', '--']\n"
            "print(json.dumps({'type': 'turn.started', 'prompt': sys.argv[-1], 'cwd': os.getcwd()}))\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        monkeypatch.setattr(run_codex, "PROJECT_ROOT", project)
        monkeypatch.setattr(
            sys, "argv", [
                "run_codex", "--cwd", str(workspace), "--codex-bin", str(executable),
                "--model", "test-model", "--", prompt,
            ],
        )

        assert run_codex.main() == 7

        (capture,) = (project / ".captures").glob("run-*")
        assert json.loads((capture / "result.json").read_text(encoding="utf-8")) == {"exit_code": 7}
        trace = (capture / "events.timed.jsonl").read_text(encoding="utf-8")
        assert json.loads(trace)["event"] == {
            "type": "turn.started", "prompt": prompt, "cwd": str(workspace),
        }
