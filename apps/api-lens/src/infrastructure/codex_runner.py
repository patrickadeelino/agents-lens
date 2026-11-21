"""Run Codex and save its timed event stream in a private capture directory."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from src.application.capture import EventCapture

from .capture_files import CaptureFiles


class CodexRunner:
    def __init__(self, capture: EventCapture, *, codex_bin: str = "codex") -> None:
        self._capture = capture
        self._codex_bin = codex_bin

    def run(
        self, prompt: str, *, cwd: Path, capture_dir: Path, model: str | None = None
    ) -> int:
        """Start a read-only Codex run and capture its JSONL output."""
        command = [
            self._codex_bin,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
        ]
        if model is not None:
            command.extend(["--model", model])
        command.extend(["--", prompt])
        return self.capture_command(command, cwd=cwd, capture_dir=capture_dir)

    def capture_command(
        self, command: Sequence[str], *, cwd: Path, capture_dir: Path
    ) -> int:
        """Stream a subprocess trace and record its exit code after it stops."""
        output = CaptureFiles(capture_dir)
        with (
            output.open_trace() as trace,
            (capture_dir / "stderr.log").open("x", encoding="utf-8") as diagnostics,
            subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=diagnostics,
                encoding="utf-8",
            ) as process,
        ):
            assert process.stdout is not None
            try:
                self._capture.write(process.stdout, trace)
                exit_code = process.wait()
            except (Exception, KeyboardInterrupt):
                process.kill()
                raise

        output.write_result(exit_code)
        return exit_code
