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
# Where the container mounts the keys of every agent.
SECRETS_DIR = Path("/run/secrets")
KEY_VARIABLE = "ANTHROPIC_API_KEY"


@dataclass(frozen=True, kw_only=True)
class ClaudeRun:
    binary: Path
    state_dir: Path
    model: str
    api_key: str = field(repr=False)
    workspace_root: Path
    workspace: Path

    def command(self, request: ExecutorRequest) -> tuple[str, ...]:
        resume = ("--resume", str(request.thread_id)) if request.thread_id else ()
        return (
            str(self.binary), "-p", "--output-format", "stream-json", "--verbose", "--model", self.model,
            "--safe-mode", "--strict-mcp-config", "--settings", json.dumps(self.settings()),
            "--tools", TOOLS, "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, *resume,
        )

    def settings(self) -> Json:
        """The sandbox covers only Bash; permission rules cover the other tools.

        By default Bash reads the whole filesystem, so everything private is denied and only the chosen folder
        is opened again inside the workspace root. The narrower path wins.
        """
        private = (self.state_dir.parent, SECRETS_DIR)
        return {
            "sandbox": {
                "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                # Needed inside an unprivileged container, where /proc cannot be mounted again.
                "enableWeakerNestedSandbox": True,
                "filesystem": {
                    "denyRead": [str(path) for path in (self.workspace_root, *private)],
                    "allowRead": [str(self.workspace)],
                },
                # Claude Code itself needs the key; the commands it runs do not.
                "credentials": {"envVars": [{"name": KEY_VARIABLE, "mode": "deny"}]},
            },
            "permissions": {"deny": [f"Read(/{path}/**)" for path in private]},
        }

    def environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "LANG": "C.UTF-8",
            "HOME": str(self.state_dir),
            KEY_VARIABLE: self.api_key,
        }
