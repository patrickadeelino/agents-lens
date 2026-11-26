"""Run one prompt in an agent process and turn its events into executor messages."""

import json
from asyncio import LimitOverrunError
from asyncio.subprocess import Process
from collections.abc import AsyncGenerator
from contextlib import aclosing

from opentelemetry.trace import Span

from src.executor.agents.base import AgentAdapter, AgentError, AgentRun
from src.shared.executor_protocol import ExecutorRequest, RunError, RunExit, RunOutput

from .agent_process import agent_process
from .execution_report import ExecutionReport

type ExecutionMessage = RunOutput | RunExit | RunError

# What a broken or unexpected exchange with an agent can raise.
EXCHANGE_FAILURES = (
    LimitOverrunError, AgentError, OSError, EOFError, TimeoutError,
    ValueError, KeyError, TypeError, AttributeError,
)


async def execution_messages(
    adapter: AgentAdapter, run: AgentRun, request: ExecutorRequest, span: Span,
) -> AsyncGenerator[ExecutionMessage]:
    report = ExecutionReport(span)
    # The turn closes before its process, and the ending is sent while the process still exists.
    async with (
        agent_process(run, request) as process,
        aclosing(turn_messages(adapter, process, run, request, report)) as messages,
    ):
        async for message in messages:
            yield message


async def turn_messages(
    adapter: AgentAdapter, process: Process | None, run: AgentRun, request: ExecutorRequest, report: ExecutionReport,
) -> AsyncGenerator[ExecutionMessage]:
    if process is None:
        yield report.start_failed()
        return
    report.started()
    turn = adapter.start_turn(process)
    secret = json.dumps(run.api_key)[1:-1]
    try:
        async for event in turn.events(run, request):
            report.event_read()
            yield RunOutput(stdout=json.dumps(event, ensure_ascii=False).replace(secret, "[REDACTED]"))
        yield report.finished(turn.exit_code)
    except EXCHANGE_FAILURES as error:
        yield report.failed(error)
