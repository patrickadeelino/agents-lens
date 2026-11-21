"""Stream subprocess output from the executor service."""

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from src.api.schemas import PromptRequest
from src.infrastructure.executor_protocol import RunError, RunExit, RunOutput
from src.infrastructure.read_only_run import ReadOnlyRun

MAX_EVENT_BYTES = 8 * 1024 * 1024

app = FastAPI(title="Agent Lens Executor")
execution_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


async def stream_process(run: ReadOnlyRun, prompt: str) -> AsyncIterator[str]:
    if execution_lock.locked():
        logger.info("Executor rejected a concurrent run")
        yield RunExit(exit_code=None).model_dump_json() + "\n"
        return

    async with execution_lock, aclosing(subprocess_messages(run, prompt)) as messages:
        async for message in messages:
            yield message.model_dump_json() + "\n"


async def send_prompt(stdin: asyncio.StreamWriter, prompt: str) -> None:
    try:
        with suppress(BrokenPipeError, ConnectionResetError):
            stdin.write(prompt.encode("utf-8"))
            await stdin.drain()
    finally:
        stdin.close()


async def stdout_lines(stdout: asyncio.StreamReader) -> AsyncIterator[bytes]:
    while True:
        try:
            line = await stdout.readuntil(b"\n")
        except asyncio.IncompleteReadError as error:
            if error.partial:
                yield error.partial
            return
        yield line


async def stop_process(process: asyncio.subprocess.Process, stdout: asyncio.StreamReader) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    # Drain the paused pipe before waiting for its transport to close.
    while await stdout.read(64 * 1024):
        pass
    await process.wait()


async def subprocess_messages(
    run: ReadOnlyRun, prompt: str,
) -> AsyncGenerator[RunOutput | RunExit | RunError]:
    try:
        process = await asyncio.create_subprocess_exec(
            *run.command(),
            cwd=run.workspace,
            env=run.environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_EVENT_BYTES,
        )
    except OSError as error:
        logger.warning("Executor process startup failed (%s)", type(error).__name__)
        yield RunExit(exit_code=None)
        return

    assert process.stdout is not None and process.stdin is not None
    delivery = asyncio.create_task(send_prompt(process.stdin, prompt))
    try:
        async for line in stdout_lines(process.stdout):
            stdout = line.decode("utf-8").replace(run.api_key, "[REDACTED]")
            yield RunOutput(stdout=stdout)
        await delivery
        yield RunExit(exit_code=await process.wait())
        return
    except asyncio.LimitOverrunError:
        logger.warning("Codex event exceeded the stdout line limit of %d bytes", MAX_EVENT_BYTES)
    finally:
        delivery.cancel()
        if not process.stdout.at_eof() or process.returncode is None:
            await asyncio.shield(stop_process(process, process.stdout))

    yield RunError(error="event_too_large")


@app.post("/run", response_class=StreamingResponse)
async def prompt_run(request: PromptRequest) -> StreamingResponse:
    run = getattr(app.state, "read_only_run", None)
    if not isinstance(run, ReadOnlyRun):
        raise HTTPException(status_code=503, detail="Read-only executor is not configured.")
    return StreamingResponse(
        stream_process(run, request.prompt),
        media_type="application/x-ndjson",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the host read-only executor on a Unix socket.")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--codex-bin", required=True, type=Path)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    key = sys.stdin.readline().strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        parser.error("Provide the Codex API key via standard input.")
    app.state.read_only_run = ReadOnlyRun(
        binary=args.codex_bin.resolve(strict=True),
        workspace=args.workspace.resolve(strict=True),
        state_dir=args.state_dir.resolve(strict=True),
        model=args.model,
        api_key=key,
    )
    uvicorn.run(app, uds=args.socket, access_log=False)


if __name__ == "__main__":
    main()
