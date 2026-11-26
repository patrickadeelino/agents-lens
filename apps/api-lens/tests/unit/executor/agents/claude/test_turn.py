import asyncio
import json
from asyncio import IncompleteReadError, StreamReader
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from src.executor.agents.base import AgentError
from src.executor.agents.claude.turn import ClaudeTurn
from src.shared.executor_protocol import ExecutorRequest

REQUEST = ExecutorRequest(workspace="/workspace", prompt="Fix the test")
SUCCESS = b'{"type": "result", "is_error": false, "terminal_reason": "completed"}\n'
FAILURE = b'{"type": "result", "is_error": true}\n'


class FakeInput:
    def __init__(self) -> None:
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, output: bytes, returncode: int) -> None:
        self.stdin = FakeInput()
        self.output = output
        self.returncode = returncode
        self.stdout: StreamReader

    async def wait(self) -> int:
        return self.returncode


def run_turn(output: bytes, returncode: int = 0) -> tuple[ClaudeTurn, FakeProcess, list[dict[str, Any]]]:
    process = FakeProcess(output, returncode)

    async def collect() -> tuple[ClaudeTurn, list[dict[str, Any]]]:
        process.stdout = StreamReader()
        process.stdout.feed_data(process.output)
        process.stdout.feed_eof()
        turn = ClaudeTurn(cast(Any, process))
        return turn, [event async for event in turn.events(None, REQUEST)]

    turn, events = asyncio.run(collect())
    return turn, process, events


def test_the_prompt_goes_through_standard_input_which_then_closes() -> None:
    _, process, _ = run_turn(SUCCESS)
    assert (process.stdin.written, process.stdin.closed) == (b"Fix the test", True)


@pytest.mark.parametrize(("output", "returncode", "ending", "exit_code"), [
    (SUCCESS, 0, "turn.completed", 0),
    (SUCCESS, 2, "turn.completed", 2),
    (FAILURE, 0, "turn.failed", 1),
    (FAILURE, 3, "turn.failed", 3),
])
def test_success_needs_both_a_clean_result_and_a_clean_exit(
    output: bytes, returncode: int, ending: str, exit_code: int,
) -> None:
    turn, _, events = run_turn(output, returncode)
    assert (events[-1]["type"], turn.exit_code) == (ending, exit_code)


@pytest.mark.parametrize(("returncode", "exit_code"), [(0, None), (1, 1)])
def test_a_process_that_ends_without_a_result_never_confirms_success(
    returncode: int, exit_code: int | None,
) -> None:
    turn, _, events = run_turn(b"", returncode)
    assert (events, turn.exit_code) == ([], exit_code)


@pytest.mark.parametrize(("output", "error"), [
    (b"not json\n", ValueError), (b"[1]\n", AgentError), (b'{"type": "result"', IncompleteReadError),
])
def test_broken_output_stops_the_turn(output: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        run_turn(output)


def test_a_resumed_turn_checks_the_session_it_asked_for() -> None:
    init = json.dumps({"type": "system", "subtype": "init", "session_id": str(UUID(int=2))}).encode() + b"\n"
    process = FakeProcess(init, 0)
    request = REQUEST.model_copy(update={"thread_id": UUID(int=1), "workspace": str(Path("/workspace"))})

    async def consume() -> None:
        process.stdout = StreamReader()
        process.stdout.feed_data(process.output)
        process.stdout.feed_eof()
        async for _ in ClaudeTurn(cast(Any, process)).events(None, request):
            pass

    with pytest.raises(AgentError):
        asyncio.run(consume())
