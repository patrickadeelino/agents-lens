"""Add monotonic arrival offsets to Codex CLI JSONL read from stdin."""

import sys

from src.application.capture import EventCapture


def main() -> None:
    EventCapture().write(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
