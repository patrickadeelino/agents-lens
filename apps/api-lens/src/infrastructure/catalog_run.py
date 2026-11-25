"""Configure the host Codex subprocess and its fixed writable catalog sandbox."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROFILE = "agent-lens-catalog"


@dataclass(frozen=True)
class CatalogRun:
    binary: Path
    workspace: Path
    state_dir: Path
    model: str
    api_key: str = field(repr=False)

    def command(self) -> tuple[str, ...]:
        workspace = json.dumps(str(self.workspace))
        binary = json.dumps(str(self.binary))
        filesystem = f'{{":root" = "deny", ":minimal" = "read", {workspace} = "write", {binary} = "read"}}'
        return (
            str(self.binary), "app-server", "--listen", "stdio://", "--strict-config",
            "-c", f'default_permissions="{PROFILE}"',
            "-c", f"permissions.{PROFILE}.filesystem={filesystem}",
            "-c", f"permissions.{PROFILE}.network.enabled=false",
            "-c", 'shell_environment_policy.inherit="none"',
            "-c", 'shell_environment_policy.set.PATH="/usr/bin:/bin"',
            "-c", 'approval_policy="never"',
            "-c", 'cli_auth_credentials_store="ephemeral"',
        )

    def environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "LANG": "C.UTF-8",
            "HOME": str(self.state_dir),
            "CODEX_HOME": str(self.state_dir),
        }
