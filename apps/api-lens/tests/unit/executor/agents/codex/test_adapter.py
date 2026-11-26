from asyncio import StreamReader, StreamWriter
from asyncio.subprocess import Process
from typing import cast

from src.executor.agents.base import AgentAdapter
from src.executor.agents.codex.adapter import CodexAdapter
from src.executor.agents.codex.app_server import AppServerTurn


class FakeProcess:
    stdin = cast(StreamWriter, object())
    stdout = cast(StreamReader, object())


def test_the_codex_adapter_drives_a_process_through_app_server() -> None:
    adapter: AgentAdapter = CodexAdapter()
    turn = adapter.start_turn(cast(Process, FakeProcess()))
    assert adapter.name == "codex"
    assert isinstance(turn, AppServerTurn)
    assert turn.exit_code is None
