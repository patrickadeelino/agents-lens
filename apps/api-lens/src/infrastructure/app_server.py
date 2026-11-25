"""Translate a single App Server turn into the existing capture events."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from src.infrastructure.catalog_run import PROFILE, CatalogRun
from src.infrastructure.executor_protocol import ExecutorRequest

Json = dict[str, Any]


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
            while True:
                message = await self.receive()
                if message.get("id") == request_id and "method" not in message:
                    return request_result(message)
                if pending is not None and "id" not in message:
                    pending.append(message)
                    if len(pending) > 128:
                        raise AppServerError

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

    async def events(self, run: CatalogRun, request: ExecutorRequest) -> AsyncIterator[Json]:
        thread_id = await self.open_thread(run, request)
        pending: list[Json] = []
        await self.send("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": request.prompt, "text_elements": []}],
        }, 4)
        turn_id = (await self.reply(4, pending))["turn"]["id"]
        yield {"type": "thread.started", "thread_id": thread_id}
        while self.exit_code is None:
            message = pending.pop(0) if pending else await self.receive()
            params = message.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if (params.get("turnId") or params.get("turn", {}).get("id")) != turn_id:
                continue
            event = self.turn_event(message["method"], params)
            if event is not None:
                yield event
