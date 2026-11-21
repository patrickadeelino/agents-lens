"""Run Codex and save its timed event stream in a private capture directory."""

import argparse
from pathlib import Path
from tempfile import mkdtemp

from src.capture import EventCapture
from src.codex_runner import CodexRunner

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Prompt to send to Codex")
    parser.add_argument("--cwd", required=True, type=Path, help="Working directory for Codex")
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI executable")
    parser.add_argument("--model", help="Model to select for this run")
    args = parser.parse_args()

    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        parser.error(f"working directory does not exist: {cwd}")

    try:
        capture_root = PROJECT_ROOT / ".captures"
        capture_root.mkdir(mode=0o700, exist_ok=True)
        capture_dir = Path(mkdtemp(prefix="run-", dir=capture_root))
        print(f"Trace: {capture_dir / 'events.timed.jsonl'}", flush=True)
        runner = CodexRunner(EventCapture(), codex_bin=args.codex_bin)
        exit_code = runner.run(args.prompt, cwd=cwd, capture_dir=capture_dir, model=args.model)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Capture failed: {error}\n")

    print(f"Codex exit code: {exit_code}")
    return 128 - exit_code if exit_code < 0 else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
