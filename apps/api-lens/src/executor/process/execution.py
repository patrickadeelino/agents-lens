"""Run one prompt in a Codex process and turn its events into executor messages."""

import json
from asyncio import LimitOverrunError
from asyncio.subprocess import Process
from collections.abc import AsyncGenerator
from contextlib import aclosing

from opentelemetry.trace import Span

from src.executor.codex.app_server import AppServerError, AppServerTurn
from src.executor.codex.catalog_run import CatalogRun
from src.shared.executor_protocol import ExecutorRequest, RunError, RunExit, RunOutput

from .codex_process import codex_process
from .execution_report import ExecutionReport

type ExecutionMessage = RunOutput | RunExit | RunError

# What a broken or unexpected exchange with the app server can raise.
EXCHANGE_FAILURES = (
    LimitOverrunError, AppServerError, OSError, EOFError, TimeoutError,
    ValueError, KeyError, TypeError, AttributeError,
)


async def execution_messages(
    run: CatalogRun, request: ExecutorRequest, span: Span,
) -> AsyncGenerator[ExecutionMessage]:
    report = ExecutionReport(span)
    # The turn closes before its process, and the ending is sent while the process still exists.
    async with codex_process(run) as process, aclosing(turn_messages(process, run, request, report)) as messages:
        async for message in messages:
            yield message


async def turn_messages(
    process: Process | None, run: CatalogRun, request: ExecutorRequest, report: ExecutionReport,
) -> AsyncGenerator[ExecutionMessage]:
    if process is None:
        yield report.start_failed()
        return
    assert process.stdout is not None and process.stdin is not None
    report.started()
    turn = AppServerTurn(process.stdin, process.stdout)
    secret = json.dumps(run.api_key)[1:-1]
    try:
        async for event in turn.events(run, request):
            report.event_read()
            yield RunOutput(stdout=json.dumps(event, ensure_ascii=False).replace(secret, "[REDACTED]"))
        yield report.finished(turn.exit_code)
    except EXCHANGE_FAILURES as error:
        yield report.failed(error)
