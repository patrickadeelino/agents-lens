"""Stream subprocess output from the executor service."""

import argparse
import asyncio
import shutil
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, suppress
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry.trace import Span

from src.api.observability import observed_app
from src.api.sse import ClosingStreamingResponse
from src.infrastructure.catalog_run import CatalogRun
from src.infrastructure.executor_protocol import (
    SESSION_PERSISTENCE_HEADER,
    ExecutorRequest,
    RunError,
    RunExit,
    RunOutput,
)
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


async def stream_process(run: CatalogRun, request: ExecutorRequest) -> AsyncGenerator[str]:
    if execution_lock.locked():
        event_log("executor.rejected", "executor", "rejected", error_code="executor_busy")
        yield RunExit(exit_code=None).model_dump_json() + "\n"
        return

    async with execution_lock:
        with operation_span("codex.subprocess") as span:
            first_event_sent = False
            async with aclosing(subprocess_messages(run, request, span)) as messages:
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
    run: CatalogRun, request: ExecutorRequest, span: Span,
) -> AsyncGenerator[RunOutput | RunExit | RunError]:
    progress = EventProgress("executor.subprocess.first_event_read", "subprocess")
    try:
        process = await asyncio.create_subprocess_exec(
            *run.command(request.thread_id),
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
    delivery = asyncio.create_task(send_prompt(process.stdin, request.prompt))
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
async def prompt_run(request: ExecutorRequest) -> StreamingResponse:
    run = getattr(app.state, "catalog_run", None)
    if not isinstance(run, CatalogRun):
        event_log("executor.rejected", "executor", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Read-only executor is not configured.")
    return ClosingStreamingResponse(
        stream_process(run, request),
        media_type="application/x-ndjson",
        headers={SESSION_PERSISTENCE_HEADER: "enabled"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the host Catalog API executor on its fixed Unix socket.")
    parser.add_argument("--codex-bin", required=True, type=Path)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    key = sys.stdin.readline().strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        parser.error("Provide the Codex API key via standard input.")
    workspace, state_dir, socket = prepare_catalog_lab(Path(__file__).resolve().parents[2])
    app.state.catalog_run = CatalogRun(
        binary=args.codex_bin.resolve(strict=True),
        workspace=workspace,
        state_dir=state_dir,
        model=args.model,
        api_key=key,
    )
    uvicorn.run(app, uds=str(socket), access_log=False)


def prepare_catalog_lab(project: Path) -> tuple[Path, Path, Path]:
    project = project.resolve(strict=True)
    lab = project / ".local" / "codex-executor" / "catalog"
    workspace, state, ipc = (lab / name for name in ("workspace", "state", "ipc"))
    for path in (lab, workspace, state, ipc):
        if path.resolve() != path:
            raise ValueError("Catalog lab paths must not contain symlinks.")
    lab.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.mkdir(mode=0o700, exist_ok=True)
    ipc.mkdir(mode=0o700, exist_ok=True)
    # The backend container uses a different UID and mounts only this directory.
    ipc.chmod(0o711)
    if not workspace.exists():
        shutil.copytree(project / "examples" / "catalog-api", workspace)
    socket = ipc / "executor.sock"
    if socket.is_symlink():
        raise ValueError("Executor socket must not be a symlink.")
    return workspace.resolve(strict=True), state.resolve(strict=True), socket


if __name__ == "__main__":
    main()
