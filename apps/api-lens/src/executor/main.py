"""Stream subprocess output from the executor service."""

import asyncio
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from uuid import UUID

import uvicorn
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from opentelemetry.trace import Span

from src.executor.agents.base import AgentAdapter, AgentRun
from src.executor.agents.codex.adapter import CodexAdapter
from src.executor.agents.configured import ConfiguredAgent, ExecutorSetup
from src.executor.process.execution import ExecutionMessage, execution_messages
from src.executor.startup import executor_parser, executor_setup
from src.executor.workspace.workspace_folders import (
    FolderUnavailable,
    list_folders,
    resolve_folder,
)
from src.executor.workspace.workspace_selections import WorkspaceSelections
from src.shared.executor_protocol import (
    AgentName,
    AvailableAgents,
    ExecutorRequest,
    FolderListing,
    FolderRequest,
    RunExit,
    RunOutput,
    WorkspaceCheck,
    WorkspaceSelection,
)
from src.shared.observability import event_log, operation_span
from src.shared.observed_app import observed_app
from src.shared.streaming import ClosingStreamingResponse

CODEX = CodexAdapter()

app = observed_app(
    "Agent Lens Executor", "agent-lens-executor", {("POST", "/run"): "executor.http"}, private=True,
)
execution_lock = asyncio.Lock()
workspace_selections = WorkspaceSelections()


def configured_setup() -> ExecutorSetup:
    setup = getattr(app.state, "setup", None)
    if not isinstance(setup, ExecutorSetup) or not setup.agents:
        event_log("executor.rejected", "executor", "rejected", error_code="executor_unconfigured")
        raise HTTPException(status_code=503, detail="Executor is not configured.")
    return setup


def configured_agent(setup: ExecutorSetup, name: AgentName) -> ConfiguredAgent:
    agent = setup.agents.get(name)
    if agent is None:
        event_log("executor.rejected", "executor", "rejected", error_code="agent_unavailable")
        raise HTTPException(status_code=409, detail="This agent is not configured.")
    return agent


def checked_workspace(path: Path, setup: ExecutorSetup) -> Path:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Workspace is not a directory.")
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail="Workspace directory is unavailable.") from error
    if not resolved.is_relative_to(setup.workspace_root):
        raise HTTPException(status_code=409, detail="Workspace directory is outside the workspace root.")
    for private in (setup.state_dir, setup.state_dir.parent / "ipc"):
        if resolved.is_relative_to(private) or private.is_relative_to(resolved):
            raise HTTPException(status_code=409, detail="Workspace overlaps private executor files.")
    return resolved


def confirmed_workspace(path: Path, setup: ExecutorSetup) -> Path:
    if checked_workspace(path, setup) != path:
        raise HTTPException(status_code=409, detail="Workspace directory changed.")
    return path


@app.get("/ready", status_code=204)
def readiness() -> Response:
    configured_setup()
    return Response(status_code=204)


@app.get("/agents")
def list_agents() -> AvailableAgents:
    return AvailableAgents(agents=list(configured_setup().agents))


@app.post("/workspace-check", status_code=204)
def validate_workspace(request: WorkspaceCheck) -> Response:
    confirmed_workspace(Path(request.workspace), configured_setup())
    return Response(status_code=204)


@app.get("/folders")
def browse_folders(path: str = "") -> FolderListing:
    root = configured_setup().workspace_root
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
    setup = configured_setup()
    try:
        resolved = checked_workspace(resolve_folder(setup.workspace_root, folder.path), setup)
    except FolderUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return WorkspaceSelection(id=workspace_selections.remember(resolved), path=str(resolved))


@app.get("/workspaces/{selection_id}")
def get_workspace(selection_id: UUID) -> WorkspaceSelection:
    path = workspace_selections.find(selection_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Workspace selection expired.")
    return WorkspaceSelection(id=selection_id, path=str(confirmed_workspace(path, configured_setup())))


class FirstEventMark:
    """Mark on the span the moment the first agent event leaves the executor."""

    def __init__(self, span: Span) -> None:
        self._span = span
        self._marked = False

    def observe(self, message: ExecutionMessage) -> None:
        if self._marked or not isinstance(message, RunOutput):
            return
        self._span.add_event("executor.stream.first_event_sent")
        self._marked = True


@asynccontextmanager
async def traced_execution(
    adapter: AgentAdapter, run: AgentRun, request: ExecutorRequest,
) -> AsyncIterator[tuple[Span, AsyncGenerator[ExecutionMessage]]]:
    """Open the execution span and its messages; the messages close before the span ends."""
    with operation_span("agent.subprocess") as span:
        span.set_attribute("agent_lens.agent", adapter.name)
        async with aclosing(execution_messages(adapter, run, request, span)) as messages:
            yield span, messages


async def stream_process(
    run: AgentRun, request: ExecutorRequest, adapter: AgentAdapter = CODEX,
) -> AsyncGenerator[str]:
    if execution_lock.locked():
        event_log("executor.rejected", "executor", "rejected", error_code="executor_busy")
        yield RunExit(exit_code=None).model_dump_json() + "\n"
        return

    async with execution_lock, traced_execution(adapter, run, request) as (span, messages):
        first_event = FirstEventMark(span)
        async for message in messages:
            yield message.model_dump_json() + "\n"
            # The iterator resumes after ASGI accepts the response body.
            first_event.observe(message)


@app.post("/run", response_class=StreamingResponse)
async def prompt_run(request: ExecutorRequest) -> StreamingResponse:
    setup = configured_setup()
    agent = configured_agent(setup, request.agent)
    run = agent.run_in(confirmed_workspace(Path(request.workspace), setup))
    return ClosingStreamingResponse(
        stream_process(run, request, agent.adapter),
        media_type="application/x-ndjson",
    )


def main() -> None:
    parser = executor_parser()
    args = parser.parse_args()
    codex_input = [sys.stdin.readline()]
    state_dir, socket = prepare_executor_dirs(args.data_dir)
    try:
        app.state.setup = executor_setup(args, codex_input, state_dir)
    except ValueError as error:
        parser.error(str(error))
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
