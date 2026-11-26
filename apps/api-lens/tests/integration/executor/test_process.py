import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from src.executor.agents.codex.catalog_run import CatalogRun
from src.executor.main import execution_lock, stream_process
from src.shared.executor_protocol import ExecutorRequest

APP_SERVER = """
import json, os, sys, time
from pathlib import Path

state = Path(os.environ["CODEX_HOME"])
mode = (state / "mode").read_text()
(state / "pid").write_text(str(os.getpid()))
(state / "runtime-dir").write_text(os.environ["TMPDIR"])


def request():
    return json.loads(sys.stdin.readline())


def reply(result=None, **message):
    message = message or {"result": result}
    print(json.dumps({"id": opened["id"], **message}), flush=True)


opened = request()
if mode == "reject":
    reply(error={"code": -32600})
    time.sleep(30)
reply({})
request()
opened = request()
reply({"type": "apiKey"})
opened = request()
reply({"thread": {"id": "thread"}, "cwd": os.getcwd(), "approvalPolicy": "never",
       "activePermissionProfile": {"id": "agent-lens-catalog"}})
opened = request()
reply({"turn": {"id": "turn"}})
if mode == "exit":
    sys.exit(0)
if mode == "oversized":
    print("x" * (9 * 1024 * 1024), flush=True)
time.sleep(30)
"""


@pytest.fixture
def run(tmp_path: Path) -> CatalogRun:
    binary = tmp_path / "app-server"
    binary.write_text(f"#!{sys.executable}\n" + APP_SERVER, encoding="utf-8")
    binary.chmod(0o700)
    return CatalogRun(
        binary=binary, state_dir=tmp_path, workspace=tmp_path, workspace_root=tmp_path,
        model="test-model", api_key="not-a-real-key",
    )


def stream(run: CatalogRun, mode: str) -> AsyncGenerator[str]:
    (run.state_dir / "mode").write_text(mode)
    return stream_process(run, ExecutorRequest(prompt="hello", workspace=str(run.workspace)))


def assert_released(run: CatalogRun) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(int((run.state_dir / "pid").read_text()), 0)
    assert not Path((run.state_dir / "runtime-dir").read_text()).exists()
    assert not execution_lock.locked()


@pytest.mark.parametrize(("mode", "ending"), [
    ("reject", {"exit_code": 1}),
    ("exit", {"exit_code": None}),
    ("oversized", {"error": "event_too_large"}),
])
def test_a_failed_execution_reports_its_ending_and_releases_everything(
    run: CatalogRun, mode: str, ending: dict[str, object],
) -> None:
    async def collect() -> list[str]:
        return [line async for line in stream(run, mode)]

    assert json.loads(asyncio.run(collect())[-1]) == ending
    assert_released(run)


def test_closing_the_stream_stops_the_process_and_releases_everything(run: CatalogRun) -> None:
    async def read_one() -> None:
        lines = stream(run, "hang")
        assert "thread.started" in await anext(lines)
        assert execution_lock.locked()
        await lines.aclose()
        assert_released(run)

    asyncio.run(read_one())


def test_cancelling_the_reader_stops_the_process_and_releases_everything(run: CatalogRun) -> None:
    async def cancel() -> None:
        lines = stream(run, "hang")
        await anext(lines)
        waiting = asyncio.ensure_future(anext(lines))
        await asyncio.sleep(0.1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await lines.aclose()
        assert_released(run)

    asyncio.run(cancel())


def test_a_process_that_cannot_start_releases_the_executor(run: CatalogRun) -> None:
    lines_from = stream(run, "hang")
    run.binary.unlink()

    async def collect() -> list[str]:
        return [line async for line in lines_from]

    assert [json.loads(line) for line in asyncio.run(collect())] == [{"exit_code": None}]
    assert not execution_lock.locked()
