import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from src.capture import EventCapture
from src.codex_runner import CodexRunner


def test_capture_writes_events_before_process_exit_and_records_diagnostics() -> None:
    capture_root = Path(__file__).resolve().parents[3] / ".captures"
    capture_root.mkdir(mode=0o700, exist_ok=True)
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
