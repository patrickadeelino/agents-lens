"""Run turns through the Claude Code command line."""

from asyncio.subprocess import Process
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.executor.agents.configured import ConfiguredAgent

from .run import ClaudeRun
from .turn import ClaudeTurn


@dataclass(frozen=True, kw_only=True)
class ClaudeConfig:
    binary: Path
    state_dir: Path
    model: str
    api_key: str = field(repr=False)
    workspace_root: Path


class ClaudeCodeAdapter:
    name = "claude_code"

    def start_turn(self, process: Process) -> ClaudeTurn:
        return ClaudeTurn(process)


def claude_agent(config: ClaudeConfig) -> ConfiguredAgent:
    def run_in(workspace: Path) -> ClaudeRun:
        return ClaudeRun(**asdict(config), workspace=workspace)

    return ConfiguredAgent(ClaudeCodeAdapter(), run_in)
