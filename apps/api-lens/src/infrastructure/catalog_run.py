"""Configure the host Codex subprocess and its fixed writable catalog sandbox."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

PROFILE = "agent-lens-catalog"


@dataclass(frozen=True)
class CatalogRun:
    binary: Path
    workspace: Path
    state_dir: Path
    model: str
    api_key: str = field(repr=False)

    def command(self, thread_id: UUID | None = None) -> tuple[str, ...]:
        workspace = json.dumps(str(self.workspace))
        binary = json.dumps(str(self.binary))
        filesystem = f'{{":root" = "deny", ":minimal" = "read", {workspace} = "write", {binary} = "read"}}'
        return (
            str(self.binary), "exec", *(("resume",) if thread_id else ()),
            "--json", "--skip-git-repo-check",
            "--strict-config", "--ignore-user-config", "--model", self.model,
            "-c", f'default_permissions="{PROFILE}"',
            "-c", f"permissions.{PROFILE}.filesystem={filesystem}",
            "-c", f"permissions.{PROFILE}.network.enabled=false",
            "-c", 'shell_environment_policy.inherit="none"',
            "-c", 'shell_environment_policy.set.PATH="/usr/bin:/bin"',
            "-c", 'approval_policy="never"',
            "--", *((str(thread_id),) if thread_id else ()), "-",
        )

    def environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "LANG": "C.UTF-8",
            "HOME": str(self.state_dir),
            "CODEX_HOME": str(self.state_dir),
            "CODEX_API_KEY": self.api_key,
        }
