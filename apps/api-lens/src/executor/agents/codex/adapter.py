"""Run turns through Codex App Server."""

from asyncio.subprocess import Process

from .app_server import AppServerTurn


class CodexAdapter:
    name = "codex"

    def start_turn(self, process: Process) -> AppServerTurn:
        assert process.stdin is not None and process.stdout is not None
        return AppServerTurn(process.stdin, process.stdout)
