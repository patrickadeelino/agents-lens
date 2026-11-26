"""Run turns through the Claude Code command line."""

from asyncio.subprocess import Process

from .turn import ClaudeTurn


class ClaudeCodeAdapter:
    name = "claude_code"

    def start_turn(self, process: Process) -> ClaudeTurn:
        return ClaudeTurn(process)
