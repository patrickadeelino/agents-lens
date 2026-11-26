from asyncio.subprocess import Process
from typing import cast

from src.executor.agents.base import AgentAdapter
from src.executor.agents.claude.adapter import ClaudeCodeAdapter
from src.executor.agents.claude.turn import ClaudeTurn


def test_the_claude_code_adapter_drives_a_process() -> None:
    adapter: AgentAdapter = ClaudeCodeAdapter()
    turn = adapter.start_turn(cast(Process, object()))
    assert adapter.name == "claude_code"
    assert isinstance(turn, ClaudeTurn)
    assert turn.exit_code is None
