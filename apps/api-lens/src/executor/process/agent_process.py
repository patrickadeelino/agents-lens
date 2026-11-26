"""Own the agent subprocess and its private runtime directory."""

import asyncio
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from tempfile import TemporaryDirectory

from src.executor.agents.base import AgentRun
from src.shared.executor_protocol import ExecutorRequest

MAX_EVENT_BYTES = 8 * 1024 * 1024


@asynccontextmanager
async def agent_process(run: AgentRun, request: ExecutorRequest) -> AsyncIterator[Process | None]:
    """Yield a running process, or None when it cannot start. Leaving stops it and removes the directory."""
    # Codex's sandbox helper requires a short, user-owned socket directory with mode 0700.
    with TemporaryDirectory(prefix="lens-codex-", dir="/tmp") as runtime_dir:
        process = await start_process(run, request, runtime_dir)
        try:
            yield process
        finally:
            await close_process(process)


async def start_process(run: AgentRun, request: ExecutorRequest, runtime_dir: str) -> Process | None:
    try:
        return await asyncio.create_subprocess_exec(
            *run.command(request),
            cwd=run.workspace,
            env={**run.environment(), "TMPDIR": runtime_dir, "XDG_RUNTIME_DIR": runtime_dir},
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
            limit=MAX_EVENT_BYTES,
        )
    except OSError:
        return None


async def close_process(process: Process | None) -> None:
    if process is None:
        return
    assert process.stdout is not None and process.stdin is not None
    process.stdin.close()
    # The process must be reaped even when the request is cancelled.
    await asyncio.shield(stop_process(process, process.stdout))


async def stop_process(process: Process, stdout: asyncio.StreamReader) -> None:
    """Give the process two seconds to leave before killing it."""
    try:
        async with asyncio.timeout(2):
            await drain_process(process, stdout)
        return
    except TimeoutError:
        pass
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await drain_process(process, stdout)


async def drain_process(process: Process, stdout: asyncio.StreamReader) -> None:
    # Drain the paused pipe before waiting for its transport to close.
    while await stdout.read(64 * 1024):
        pass
    await process.wait()
