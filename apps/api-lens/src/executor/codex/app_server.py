"""Translate a single App Server turn into the existing capture events."""

import asyncio
import json
import re
from collections import deque
from collections.abc import AsyncIterator, Iterator

from src.executor.codex.app_server_delegation import (
    DelegationCapture,
    Json,
    Notification,
)
from src.executor.codex.catalog_run import PROFILE, CatalogRun
from src.shared.executor_protocol import ExecutorRequest


class AppServerError(Exception):
    """The subprocess did not complete the expected protocol exchange."""


class AppServerRejected(AppServerError):
    """The server explicitly rejected a request."""


def request_result(message: Json) -> Json:
    if "error" in message:
        raise AppServerRejected
    result = message.get("result")
    if not isinstance(result, dict):
        raise AppServerError
    return result


def snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def item_event(method: str, item: Json) -> Json | None:
    kind = item["type"]
    if kind in {"userMessage", "subAgentActivity"}:
        return None
    fields = {snake(key): value for key, value in item.items()}
    fields["type"] = "collab_tool_call" if kind == "collabAgentToolCall" else snake(kind)
    if fields.get("status") == "inProgress":
        fields["status"] = "in_progress"
    if kind == "reasoning":
        fields["text"] = "\n".join(item.get("summary", []))
    return {"type": method.replace("/", "."), "item": fields}


def usage_delta(total: Json, baseline: Json) -> Json:
    return {
        key: value - baseline[key]
        for key, value in total.items()
        if key in baseline and type(value) is int and type(baseline[key]) is int and value >= baseline[key] >= 0
    }


def answers(message: Json, request_id: int) -> bool:
    return message.get("id") == request_id and "method" not in message


def hold_notification(message: Json, pending: list[Json] | None) -> None:
    """Keep a notification that arrived before the reply, up to a limit."""
    if pending is None or "id" in message:
        return
    pending.append(message)
    if len(pending) > 128:
        raise AppServerError


class AppServerTurn:
    def __init__(self, stdin: asyncio.StreamWriter, stdout: asyncio.StreamReader) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.exit_code: int | None = None
        self.usage: Json = {}
        self.usage_baseline: Json | None = None
        self.resuming = False

    async def send(self, method: str, params: Json, request_id: int | None = None) -> None:
        message: Json = {"method": method, "params": params}
        if request_id is not None:
            message["id"] = request_id
        self.stdin.write(json.dumps(message).encode() + b"\n")
        await self.stdin.drain()

    async def receive(self) -> Json:
        line = await self.stdout.readuntil(b"\n")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise AppServerError
        if "method" in message and "id" in message:
            # Never grant a server-initiated request through the unattended executor.
            response = {"id": message["id"], "error": {"code": -32601, "message": "Unsupported request"}}
            self.stdin.write(json.dumps(response).encode() + b"\n")
            await self.stdin.drain()
        return message

    async def reply(self, request_id: int, pending: list[Json] | None = None) -> Json:
        async with asyncio.timeout(30):
            message = await self.receive()
            while not answers(message, request_id):
                hold_notification(message, pending)
                message = await self.receive()
            return request_result(message)

    async def authenticate(self, api_key: str) -> None:
        await self.send("account/login/start", {"type": "apiKey", "apiKey": api_key}, 3)
        if (await self.reply(3)).get("type") != "apiKey":
            raise AppServerError

    async def open_thread(self, run: CatalogRun, request: ExecutorRequest) -> str:
        await self.send("initialize", {
            "clientInfo": {"name": "agent-lens", "version": "1"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False},
        }, 1)
        await self.reply(1)
        await self.send("initialized", {})
        await self.authenticate(run.api_key)

        params: Json = {
            "cwd": str(run.workspace), "model": run.model, "approvalPolicy": "never", "permissions": PROFILE,
        }
        method = "thread/start"
        if request.thread_id:
            self.resuming = True
            method = "thread/resume"
            params.update(threadId=str(request.thread_id), excludeTurns=True)
        await self.send(method, params, 2)
        result = await self.reply(2)
        if result.get("approvalPolicy") != "never" or (result.get("activePermissionProfile") or {}).get("id") != PROFILE:
            raise AppServerError
        if result.get("cwd") != str(run.workspace):
            raise AppServerError
        thread_id = result["thread"]["id"]
        if not isinstance(thread_id, str) or (request.thread_id and thread_id != str(request.thread_id)):
            raise AppServerError
        return thread_id

    def turn_event(self, method: str, params: Json) -> Json | None:
        if method == "turn/started":
            return {"type": "turn.started"}
        if method in {"item/started", "item/completed"}:
            return item_event(method, params["item"])
        if method == "thread/tokenUsage/updated":
            self.update_usage(params["tokenUsage"])
        if method == "error" and not params.get("willRetry", False):
            return {"type": "error", "message": params["error"]["message"]}
        if method == "turn/completed":
            return self.complete(params["turn"])
        return None

    def update_usage(self, usage: Json) -> None:
        total = usage["total"]
        if not self.resuming:
            self.usage = total
            return
        if self.usage_baseline is None:
            # The first update's last sample belongs to this turn. Its total also includes earlier turns.
            self.usage_baseline = usage_delta(total, usage.get("last", {}))
        self.usage = usage_delta(total, self.usage_baseline)

    def complete(self, turn: Json) -> Json:
        if turn["status"] == "completed":
            self.exit_code = 0
            event: Json = {"type": "turn.completed"}
            if self.usage:
                event["usage"] = {snake(key): value for key, value in self.usage.items()}
            return event
        self.exit_code = 1
        error = turn.get("error") or {}
        return {"type": "turn.failed", "error": {"message": error.get("message") or turn["status"]}}

    def scoped_events(self, capture: DelegationCapture, note: Notification) -> Iterator[Json]:
        if capture.is_terminal(note):
            yield from capture.incomplete()
        event = self.scoped_event(capture, note)
        if event is not None and (not event["type"].startswith("delegation.") or capture.unique(event)):
            yield event
        for state in wait_observations(capture, note):
            if capture.unique(state):
                yield state

    def scoped_event(self, capture: DelegationCapture, note: Notification) -> Json | None:
        if note.item.get("type") == "subAgentActivity":
            return delegation_activity(capture, note)
        if capture.is_root(note):
            return self.turn_event(note.method, note.params)
        return child_observation(capture, note)

    async def notifications(self, capture: DelegationCapture, pending: list[Json]) -> AsyncIterator[Notification]:
        initial = deque(pending)
        while self.exit_code is None:
            for accepted in await self.next_notifications(capture, initial):
                yield accepted

    async def next_notifications(self, capture: DelegationCapture, initial: deque[Json]) -> list[Notification]:
        """Take one message, or release what the capture holds once its deadline passes."""
        try:
            async with asyncio.timeout_at(capture.deadline):
                message = initial.popleft() if initial else await self.receive()
        except TimeoutError:
            return capture.flush()
        note = Notification.from_message(message)
        return [] if note is None else capture.offer(note, asyncio.get_running_loop().time())

    async def events(self, run: CatalogRun, request: ExecutorRequest) -> AsyncIterator[Json]:
        thread_id = await self.open_thread(run, request)
        pending: list[Json] = []
        await self.send("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": request.prompt, "text_elements": []}],
        }, 4)
        turn_id = (await self.reply(4, pending))["turn"]["id"]
        capture = DelegationCapture(thread_id, turn_id)
        yield {"type": "thread.started", "thread_id": thread_id}
        async for event in self.capture_events(capture, pending):
            yield event

    async def live_events(self, capture: DelegationCapture, pending: list[Json]) -> AsyncIterator[Json]:
        async for note in self.notifications(capture, pending):
            for event in self.scoped_events(capture, note):
                yield event

    async def capture_events(self, capture: DelegationCapture, pending: list[Json]) -> AsyncIterator[Json]:
        """Yield the turn's events and, if the capture breaks, what was still held before failing."""
        try:
            async for event in self.live_events(capture, pending):
                yield event
        except Exception:
            for event in self.interrupted_events(capture):
                yield event
            raise

    def interrupted_events(self, capture: DelegationCapture) -> Iterator[Json]:
        for note in capture.flush():
            if not capture.is_terminal(note):
                yield from self.scoped_events(capture, note)
        yield from capture.incomplete()


def delegation_activity(capture: DelegationCapture, note: Notification) -> Json | None:
    item = note.item
    child = item.get("agentThreadId")
    if not isinstance(child, str) or capture.parents.get(child) != note.thread:
        return None
    if note.spawned_thread:
        return {
            "type": "delegation.edge", "parent_thread_id": note.thread, "parent_turn_id": note.turn,
            "item_id": item["id"], "child_thread_id": child, "child_index": capture.ordinal(child),
        }
    if note.method != "item/completed" or item.get("kind") != "completed":
        return None
    capture.finished.add(child)
    return {
        "type": "delegation.event", "thread_id": child, "source_thread_id": note.thread,
        "turn_id": note.turn, "item_id": item.get("id"), "observation": {
            "type": "subagent.completed", "item": item,
        },
    }


def child_turn_observation(capture: DelegationCapture, note: Notification) -> Json:
    turn = note.params["turn"]
    capture.finished.add(note.thread)
    event: Json = {"type": "turn.completed", "status": turn["status"]}
    if turn.get("error"):
        event["error"] = turn["error"]
    return event


def child_observation(capture: DelegationCapture, note: Notification) -> Json | None:
    event: Json | None = None
    if note.method in {"item/started", "item/completed"}:
        event = item_event(note.method, note.item)
    elif note.method == "turn/started":
        capture.finished.discard(note.thread)
        event = {"type": "turn.started"}
    elif note.method == "turn/completed":
        event = child_turn_observation(capture, note)
    elif note.method == "error":
        event = {"type": "error", "error": note.params.get("error"), "will_retry": note.params.get("willRetry")}
    if event is None:
        return None
    return {
        "type": "delegation.event", "thread_id": note.thread, "turn_id": note.turn,
        "item_id": note.item.get("id"), "observation": event,
    }


def wait_observations(capture: DelegationCapture, note: Notification) -> Iterator[Json]:
    item = note.item
    if note.method != "item/completed" or item.get("type") != "collabAgentToolCall" or item.get("tool") != "wait":
        return
    for child, state in (item.get("agentsStates") or {}).items():
        if capture.parents.get(child) == note.thread and isinstance(state, dict):
            yield {
                "type": "delegation.event", "thread_id": child, "source_thread_id": note.thread,
                "turn_id": note.turn, "item_id": item.get("id"),
                "observation": {"type": "subagent.wait", "state": state},
            }
