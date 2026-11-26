"""What the executor needs from a coding agent, whichever one it runs."""

from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from src.shared.executor_protocol import ExecutorRequest

type Json = dict[str, Any]


class AgentError(Exception):
    """The agent process did not complete the expected exchange."""


class AgentRejected(AgentError):
    """The agent explicitly refused a request."""


class AgentRun(Protocol):
    """One prompt to run: where, with which secret and how to start the process."""

    @property
    def workspace(self) -> Path: ...
    @property
    def api_key(self) -> str: ...
    def command(self, request: ExecutorRequest) -> tuple[str, ...]: ...
    def environment(self) -> dict[str, str]: ...


class AgentTurn(Protocol):
    """One turn over a started process, as events in the capture's own format."""

    @property
    def exit_code(self) -> int | None: ...
    def events(self, run: Any, request: ExecutorRequest) -> AsyncIterator[Json]: ...


class AgentAdapter(Protocol):
    name: str

    def start_turn(self, process: Process) -> AgentTurn: ...
