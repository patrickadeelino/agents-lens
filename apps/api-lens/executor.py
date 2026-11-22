"""Stream subprocess output from the executor service."""

import argparse
import asyncio
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, suppress
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry.trace import Span

from src.api.observability import observed_app
from src.api.schemas import PromptRequest
from src.api.sse import ClosingStreamingResponse
from src.infrastructure.executor_protocol import RunError, RunExit, RunOutput
from src.infrastructure.read_only_run import ReadOnlyRun
from src.observability import (
    EventProgress,
    event_log,
    exit_outcome,
    finish_span,
    operation_span,
)

MAX_EVENT_BYTES = 8 * 1024 * 1024

app = observed_app(
    "Agent Lens Executor", "agent-lens-executor", {("POST", "/run"): "executor.http"}, private=True,
)
execution_lock = asyncio.Lock()


async def stream_process(run: ReadOnlyRun, prompt: str) -> AsyncGenerator[str]:
    if execution_lock.locked():
        event_log("executor.rejected", "executor", "rejected", error_code="executor_busy")
        yield RunExit(exit_code=None).model_dump_json() + "\n"
        return

    async with execution_lock:
        with operation_span("codex.subprocess") as span:
            first_event_sent = False
            async with aclosing(subprocess_messages(run, prompt, span)) as messages:
                async for message in messages:
                    yield message.model_dump_json() + "\n"
                    # The iterator resumes after ASGI accepts the response body.
                    if not first_event_sent and isinstance(message, RunOutput):
                        span.add_event("executor.stream.first_event_sent")
                        first_event_sent = True


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
    run: ReadOnlyRun, prompt: str, span: Span,
) -> AsyncGenerator[RunOutput | RunExit | RunError]:
    progress = EventProgress("executor.subprocess.first_event_read", "subprocess")
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
    except OSError:
        finish_span(span, "failed", error_code="subprocess_start_failed")
        event_log(
            "executor.subprocess.failed", "subprocess", "failed",
            error_code="subprocess_start_failed", started_at=progress.started_at, event_count=progress.count,
        )
        yield RunExit(exit_code=None)
        return

    assert process.stdout is not None and process.stdin is not None
    event_log("executor.subprocess.started", "subprocess", "started")
    delivery = asyncio.create_task(send_prompt(process.stdin, prompt))
    try:
        async for line in stdout_lines(process.stdout):
            progress.advance(span)
            stdout = line.decode("utf-8").replace(run.api_key, "[REDACTED]")
            yield RunOutput(stdout=stdout)
        await delivery
        exit_code = await process.wait()
        outcome, code = exit_outcome(exit_code)
        finish_span(span, outcome, error_code=code)
        event_log(
            f"executor.subprocess.{outcome}", "subprocess", outcome,
            error_code=code, started_at=progress.started_at, exit_code=exit_code, event_count=progress.count,
        )
        yield RunExit(exit_code=exit_code)
        return
    except asyncio.LimitOverrunError:
        finish_span(span, "failed", error_code="event_too_large")
        event_log(
            "executor.subprocess.failed", "subprocess", "failed",
            error_code="event_too_large", started_at=progress.started_at, event_count=progress.count,
        )
    finally:
        delivery.cancel()
        if not process.stdout.at_eof() or process.returncode is None:
            await asyncio.shield(stop_process(process, process.stdout))

    yield RunError(error="event_too_large")


@app.post("/run", response_class=StreamingResponse)
async def prompt_run(request: PromptRequest) -> StreamingResponse:
    run = getattr(app.state, "read_only_run", None)
    if not isinstance(run, ReadOnlyRun):
        event_log("executor.rejected", "executor", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Read-only executor is not configured.")
    return ClosingStreamingResponse(
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
