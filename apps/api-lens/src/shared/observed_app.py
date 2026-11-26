"""Observe selected ASGI requests through the end of the response body."""

from asyncio import CancelledError, to_thread
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic

from fastapi import FastAPI
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind
from starlette.datastructures import Headers, State
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.shared.observability import (
    Outcome,
    Telemetry,
    event_log,
    finish_span,
    incoming_context,
    observe_operation,
    operation_span,
)


@dataclass
class Delivery:
    send: Send
    status: int | None = None
    complete: bool = False

    async def forward(self, message: Message) -> None:
        await self.send(message)
        if message["type"] == "http.response.start":
            self.status = message["status"]
        if message["type"] == "http.response.body":
            self.complete = not message.get("more_body", False)

    def result(self) -> tuple[Outcome, str | None]:
        if self.status is not None and self.status >= 500:
            return "failed", "http_error"
        if self.status is not None and self.status >= 400:
            return "rejected", "invalid_request" if self.status == 422 else "http_error"
        if self.complete:
            return "completed", None
        return "disconnected", "client_disconnected"


class ObserveHTTP:
    def __init__(
        self, app: ASGIApp, *, state: State, routes: dict[tuple[str, str], str], private: bool,
    ) -> None:
        self.app = app
        self.state = state
        self.routes = routes
        self.private = private

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method, path = scope.get("method", ""), scope.get("path", "")
        prefix = self.routes.get((method, path))
        if scope["type"] != "http" or prefix is None:
            await self.app(scope, receive, send)
            return
        context, run_id = incoming_context(dict(Headers(scope=scope))) if self.private else (Context(), None)
        with (
            observe_operation(self.state.telemetry, run_id),
            operation_span(f"{method} {path}", kind=SpanKind.SERVER, context=context) as span,
        ):
            await self._observe(span, prefix, scope, receive, Delivery(send))

    async def _observe(self, span: Span, prefix: str, scope: Scope, receive: Receive, delivery: Delivery) -> None:
        started_at = monotonic()
        outcome: Outcome = "failed"
        error_code: str | None = "http_failure"
        try:
            await self.app(scope, receive, delivery.forward)
            outcome, error_code = delivery.result()
        except (CancelledError, ClientDisconnect):
            outcome, error_code = "disconnected", "client_disconnected"
            raise
        finally:
            if delivery.status is not None:
                span.set_attribute("http.response.status_code", delivery.status)
            finish_span(span, outcome, error_code=error_code)
            event_log(f"{prefix}.{outcome}", "http", outcome, error_code=error_code, started_at=started_at)


def run_preparation(telemetry: Telemetry, prepare: Callable[[], None] | None) -> None:
    """Run what a service needs before its first request, with its events logged."""
    if prepare is None:
        return
    with observe_operation(telemetry):
        prepare()


def observed_app(
    title: str, service: str, routes: dict[tuple[str, str], str], *, private: bool = False,
    prepare: Callable[[], None] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        telemetry: Telemetry = app.state.telemetry
        telemetry.configure()
        try:
            run_preparation(telemetry, prepare)
            yield
        finally:
            await to_thread(telemetry.shutdown)

    app = FastAPI(title=title, lifespan=lifespan)
    app.state.telemetry = Telemetry(service)
    app.add_middleware(ObserveHTTP, state=app.state, routes=routes, private=private)
    return app
