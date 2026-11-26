"""Run turns through Codex App Server."""

from asyncio.subprocess import Process
from dataclasses import asdict
from pathlib import Path

from src.executor.agents.configured import ConfiguredAgent

from .app_server import AppServerTurn
from .catalog_run import CatalogRun, ExecutorConfig


class CodexAdapter:
    name = "codex"

    def start_turn(self, process: Process) -> AppServerTurn:
        assert process.stdin is not None and process.stdout is not None
        return AppServerTurn(process.stdin, process.stdout)


def codex_agent(config: ExecutorConfig) -> ConfiguredAgent:
    def run_in(workspace: Path) -> CatalogRun:
        return CatalogRun(**asdict(config), workspace=workspace)

    return ConfiguredAgent(CodexAdapter(), run_in)
