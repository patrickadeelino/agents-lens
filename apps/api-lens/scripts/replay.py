"""Replay turn and command lifecycle events from a recorded Codex CLI run."""

import argparse
import sys
from pathlib import Path

from scripts.terminal_replay import TerminalReplay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="Path to a Codex CLI JSONL trace, optionally timed")
    args = parser.parse_args()

    with args.trace.open(encoding="utf-8") as lines:
        TerminalReplay(sys.stdout).replay(lines)


if __name__ == "__main__":
    main()
