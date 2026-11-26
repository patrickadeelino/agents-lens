"""Configure the Claude Code subprocess for one workspace."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from src.executor.agents.base import Json
from src.shared.executor_protocol import ExecutorRequest

TOOLS = "Bash,Read,Edit,Write,Glob,Grep,Agent"
# Edit rules cover every tool that changes files.
ALLOWED = ("Bash", "Edit(./**)")


@dataclass(frozen=True, kw_only=True)
class ClaudeRun:
    binary: Path
    state_dir: Path
    model: str
    api_key: str = field(repr=False)
    workspace: Path

    def command(self, request: ExecutorRequest) -> tuple[str, ...]:
        resume = ("--resume", str(request.thread_id)) if request.thread_id else ()
        return (
            str(self.binary), "-p", "--output-format", "stream-json", "--verbose", "--model", self.model,
            "--safe-mode", "--strict-mcp-config", "--settings", json.dumps(self.settings()),
            "--tools", TOOLS, "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, *resume,
        )

    def settings(self) -> Json:
        """The sandbox covers only Bash; permission rules cover the other tools."""
        private = (self.state_dir, self.state_dir.parent / "ipc")
        return {
            "sandbox": {
                "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                # Needed inside an unprivileged container, where /proc cannot be mounted again.
                "enableWeakerNestedSandbox": True,
                "filesystem": {"denyRead": [str(path) for path in private]},
            },
            "permissions": {"deny": [f"Read(/{path}/**)" for path in private]},
        }

    def environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "LANG": "C.UTF-8",
            "HOME": str(self.state_dir),
            "ANTHROPIC_API_KEY": self.api_key,
        }
