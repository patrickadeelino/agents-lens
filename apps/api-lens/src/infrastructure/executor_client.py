"""Read the executor response through its Unix socket."""

from collections.abc import Iterator

import httpx
from pydantic import TypeAdapter

from src.application.capture_run import EventTooLarge, StdoutConsumer

from .executor_protocol import RunError, RunExit, RunOutput

_EXECUTOR_MESSAGE = TypeAdapter(RunOutput | RunExit | RunError)


class ExecutorClient:
    def __init__(self, socket: str) -> None:
        self._socket = socket

    def run(self, prompt: str, on_stdout: StdoutConsumer) -> int | None:
        """Deliver the stdout lines and return the terminal status."""
        transport = httpx.HTTPTransport(uds=self._socket)
        timeout = httpx.Timeout(120, connect=5.0)
        try:
            with (
                httpx.Client(
                    base_url="http://executor-host", timeout=timeout, trust_env=False, transport=transport,
                ) as client,
                client.stream("POST", "/run", json={"prompt": prompt}) as response,
            ):
                return _consume_response(response, on_stdout)
        except httpx.HTTPError as error:
            raise ConnectionError("Executor request failed.") from error


def _consume_response(response: httpx.Response, on_stdout: StdoutConsumer) -> int | None:
    terminal: RunExit | RunError | None = None

    def stdout() -> Iterator[str]:
        nonlocal terminal
        response.raise_for_status()
        for line in response.iter_lines():
            if terminal is not None:
                raise ValueError("Executor sent data after its exit status")
            message = _EXECUTOR_MESSAGE.validate_json(line)
            if isinstance(message, (RunExit, RunError)):
                terminal = message
            else:
                yield message.stdout

    on_stdout(stdout())
    return _validated_exit_code(terminal)


def _validated_exit_code(terminal: RunExit | RunError | None) -> int | None:
    if terminal is None:
        raise ValueError("Executor stream ended without an exit status")
    if isinstance(terminal, RunError):
        raise EventTooLarge
    return terminal.exit_code
