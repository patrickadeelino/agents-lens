"""Run a single Claude Code turn over a started process."""

import asyncio
import json
from asyncio import IncompleteReadError, StreamReader
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from typing import Any

from src.executor.agents.base import AgentError, Json
from src.executor.agents.claude.translation import ClaudeTranslation
from src.shared.executor_protocol import ExecutorRequest

EXIT_SECONDS = 30


class ClaudeTurn:
    def __init__(self, process: Process) -> None:
        self.process = process
        self.exit_code: int | None = None

    async def events(self, run: Any, request: ExecutorRequest) -> AsyncIterator[Json]:
        """The turn ends when the output closes, not at the first result."""
        translation = ClaudeTranslation(str(request.thread_id) if request.thread_id else None)
        await self.send_prompt(request.prompt)
        assert self.process.stdout is not None
        while line := await next_line(self.process.stdout):
            for event in translation.observe(decoded(line)):
                yield event
        async with asyncio.timeout(EXIT_SECONDS):
            returncode = await self.process.wait()
        ending = translation.ending()
        self.exit_code = confirmed_exit(ending, returncode)
        for event in closing_events(translation, ending):
            yield event

    async def send_prompt(self, prompt: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(prompt.encode())
        await self.process.stdin.drain()
        self.process.stdin.close()


async def next_line(stdout: StreamReader) -> bytes:
    """Return the next whole line, or nothing when the output closed cleanly."""
    try:
        return await stdout.readuntil(b"\n")
    except IncompleteReadError as error:
        if error.partial:
            raise
        return b""


def decoded(line: bytes) -> Json:
    message = json.loads(line)
    if not isinstance(message, dict):
        raise AgentError
    return message


def closing_events(translation: ClaudeTranslation, ending: Json | None) -> list[Json]:
    unfinished = translation.incomplete()
    return unfinished if ending is None else [*unfinished, ending]


def confirmed_exit(ending: Json | None, returncode: int) -> int | None:
    """Zero needs a completed turn and a clean process. Without a result, a clean exit confirms nothing."""
    if ending is None:
        return returncode or None
    if ending["type"] == "turn.failed":
        return returncode or 1
    return returncode
