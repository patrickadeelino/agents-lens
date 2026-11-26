"""Stream subprocess output from the executor service."""

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import uvicorn
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from opentelemetry.trace import Span

from src.api.observability import observed_app
from src.api.sse import ClosingStreamingResponse
from src.infrastructure.app_server import (
    AppServerError,
    AppServerRejected,
    AppServerTurn,
)
from src.infrastructure.catalog_run import CatalogRun, ExecutorConfig
from src.infrastructure.executor_protocol import (
    ExecutorRequest,
    FolderListing,
    FolderRequest,
    RunError,
    RunExit,
    RunOutput,
    WorkspaceCheck,
    WorkspaceSelection,
)
from src.infrastructure.workspace_folders import (
    FolderUnavailable,
    list_folders,
    resolve_folder,
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
workspace_selections: dict[UUID, Path] = {}


def configured_run() -> ExecutorConfig:
    run = getattr(app.state, "catalog_run", None)
    if not isinstance(run, ExecutorConfig):
        event_log("executor.rejected", "executor", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Executor is not configured.")
    return run


def checked_workspace(path: Path, run: ExecutorConfig) -> Path:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Workspace is not a directory.")
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail="Workspace directory is unavailable.") from error
    if not resolved.is_relative_to(run.workspace_root):
        raise HTTPException(status_code=409, detail="Workspace directory is outside the workspace root.")
    for private in (run.state_dir, run.state_dir.parent / "ipc"):
        if resolved.is_relative_to(private) or private.is_relative_to(resolved):
            raise HTTPException(status_code=409, detail="Workspace overlaps private executor files.")
    return resolved


def confirmed_workspace(path: Path, run: ExecutorConfig) -> Path:
    if checked_workspace(path, run) != path:
        raise HTTPException(status_code=409, detail="Workspace directory changed.")
    return path


@app.get("/ready", status_code=204)
def readiness() -> Response:
    configured_run()
    return Response(status_code=204)


@app.post("/workspace-check", status_code=204)
def validate_workspace(request: WorkspaceCheck) -> Response:
    confirmed_workspace(Path(request.workspace), configured_run())
    return Response(status_code=204)


@app.get("/folders")
def browse_folders(path: str = "") -> FolderListing:
    root = configured_run().workspace_root
    try:
        directory = resolve_folder(root, path)
        folders, truncated = list_folders(directory)
    except FolderUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    relative = directory.relative_to(root)
    return FolderListing(
        root=str(root), path="" if relative == Path() else relative.as_posix(),
        folders=folders, truncated=truncated,
    )


@app.post("/folders")
def select_folder(folder: FolderRequest) -> WorkspaceSelection:
    run = configured_run()
    try:
        resolved = checked_workspace(resolve_folder(run.workspace_root, folder.path), run)
    except FolderUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    selection = uuid4()
    if len(workspace_selections) >= 128:
        del workspace_selections[next(iter(workspace_selections))]
    workspace_selections[selection] = resolved
    return WorkspaceSelection(id=selection, path=str(resolved))


@app.get("/workspaces/{selection_id}")
def get_workspace(selection_id: UUID) -> WorkspaceSelection:
    path = workspace_selections.get(selection_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Workspace selection expired.")
    return WorkspaceSelection(id=selection_id, path=str(confirmed_workspace(path, configured_run())))


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


async def stop_process(process: asyncio.subprocess.Process, stdout: asyncio.StreamReader) -> None:
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


async def drain_process(process: asyncio.subprocess.Process, stdout: asyncio.StreamReader) -> None:
    # Drain the paused pipe before waiting for its transport to close.
    while await stdout.read(64 * 1024):
        pass
    await process.wait()


async def subprocess_messages(
    run: CatalogRun, request: ExecutorRequest, span: Span,
) -> AsyncGenerator[RunOutput | RunExit | RunError]:
    # Codex's sandbox helper requires a short, user-owned socket directory with mode 0700.
    with TemporaryDirectory(prefix="lens-codex-", dir="/tmp") as runtime_dir:
        async with aclosing(app_server_messages(run, request, span, runtime_dir)) as messages:
            async for message in messages:
                yield message


async def app_server_messages(
    run: CatalogRun, request: ExecutorRequest, span: Span, runtime_dir: str,
) -> AsyncGenerator[RunOutput | RunExit | RunError]:
    progress = EventProgress("executor.subprocess.first_event_read", "subprocess")
    try:
        process = await asyncio.create_subprocess_exec(
            *run.command(),
            cwd=run.workspace,
            env={**run.environment(), "TMPDIR": runtime_dir, "XDG_RUNTIME_DIR": runtime_dir},
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
    turn = AppServerTurn(process.stdin, process.stdout)
    secret = json.dumps(run.api_key)[1:-1]
    try:
        async for event in turn.events(run, request):
            progress.advance(span)
            stdout = json.dumps(event, ensure_ascii=False).replace(secret, "[REDACTED]")
            yield RunOutput(stdout=stdout)
        exit_code = turn.exit_code
        outcome, code = exit_outcome(exit_code)
        finish_span(span, outcome, error_code=code)
        event_log(
            f"executor.subprocess.{outcome}", "subprocess", outcome,
            error_code=code, started_at=progress.started_at, exit_code=exit_code, event_count=progress.count,
        )
        yield RunExit(exit_code=exit_code)
    except asyncio.LimitOverrunError:
        finish_span(span, "failed", error_code="event_too_large")
        yield RunError(error="event_too_large")
    except AppServerRejected:
        finish_span(span, "failed", error_code="app_server_request_rejected")
        event_log("executor.subprocess.failed", "subprocess", "failed", error_code="app_server_request_rejected")
        yield RunExit(exit_code=1)
    except (AppServerError, OSError, EOFError, TimeoutError, ValueError, KeyError, TypeError, AttributeError):
        finish_span(span, "failed", error_code="app_server_disconnected")
        event_log("executor.subprocess.failed", "subprocess", "failed", error_code="app_server_disconnected")
        yield RunExit(exit_code=None)
    finally:
        process.stdin.close()
        await asyncio.shield(stop_process(process, process.stdout))


@app.post("/run", response_class=StreamingResponse)
async def prompt_run(request: ExecutorRequest) -> StreamingResponse:
    config = configured_run()
    run = CatalogRun(
        binary=config.binary, state_dir=config.state_dir, model=config.model, api_key=config.api_key,
        workspace_root=config.workspace_root, workspace=confirmed_workspace(Path(request.workspace), config),
    )
    return ClosingStreamingResponse(
        stream_process(run, request),
        media_type="application/x-ndjson",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the host executor on its private Unix socket.")
    parser.add_argument("--codex-bin", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--data-dir", required=True, type=Path, help="Private Codex state and the API socket.")
    parser.add_argument(
        "--workspace-root", required=True, type=Path, help="Only this folder and its subfolders can be chosen.",
    )
    args = parser.parse_args()
    key = sys.stdin.readline().strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        parser.error("Provide the Codex API key via standard input.")
    state_dir, socket = prepare_executor_dirs(args.data_dir)
    workspace_root = args.workspace_root.resolve()
    if not workspace_root.is_dir():
        parser.error("The workspace root must be an existing folder.")
    app.state.catalog_run = ExecutorConfig(
        binary=args.codex_bin.resolve(strict=True),
        state_dir=state_dir,
        model=args.model,
        api_key=key,
        workspace_root=workspace_root,
    )
    uvicorn.run(app, uds=str(socket), access_log=False)


def prepare_executor_dirs(data_dir: Path) -> tuple[Path, Path]:
    base = data_dir.resolve()
    state, ipc = base / "state", base / "ipc"
    for path in (base, state, ipc):
        if path.resolve() != path:
            raise ValueError("Executor paths must not contain symlinks.")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.mkdir(mode=0o700, exist_ok=True)
    ipc.mkdir(mode=0o700, exist_ok=True)
    # The backend container uses a different UID and mounts only this directory.
    ipc.chmod(0o711)
    socket = ipc / "executor.sock"
    if socket.is_symlink():
        raise ValueError("Executor socket must not be a symlink.")
    return state.resolve(strict=True), socket


if __name__ == "__main__":
    main()
