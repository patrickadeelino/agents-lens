"""Track App Server delegation edges and admit attributable notifications."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

Json = dict[str, Any]
OBSERVATIONS = {"item/started", "item/completed", "turn/started", "turn/completed", "error"}
MAX_PENDING_BYTES = 8 * 1024 * 1024
MAX_PENDING_COUNT = 128
PENDING_SECONDS = 1.0


@dataclass(frozen=True)
class Notification:
    method: str
    params: Json

    @property
    def thread(self) -> str:
        return self.params["threadId"]

    @property
    def turn(self) -> str | None:
        return self.params.get("turnId") or (self.params.get("turn") or {}).get("id")

    @property
    def item(self) -> Json:
        return self.params.get("item") or {}

    @property
    def spawned_thread(self) -> str | None:
        if (
            self.method in {"item/started", "item/completed"} and self.item.get("type") == "subAgentActivity"
            and self.item.get("kind") == "started" and isinstance(self.item.get("id"), str)
        ):
            child = self.item.get("agentThreadId")
            return child if isinstance(child, str) and child else None
        return None

    @classmethod
    def from_message(cls, message: Json) -> "Notification | None":
        params = message.get("params")
        method = message.get("method")
        if "id" in message or not isinstance(params, dict) or not isinstance(method, str):
            return None
        if not isinstance(params.get("threadId"), str):
            return None
        return cls(method, params)


class DelegationCapture:
    def __init__(self, root: str, turn: str) -> None:
        self.root = root
        self.turn = turn
        self.parents: dict[str, str] = {}
        self.ordinals: dict[str, int] = {}
        self.finished: set[str] = set()
        self.pending: list[Notification] = []
        self.deadline: float | None = None
        self.pending_bytes = 0
        self.seen: set[bytes] = set()

    def is_root(self, event: Notification) -> bool:
        return event.thread == self.root and event.turn == self.turn

    def admits(self, event: Notification) -> bool:
        return self.is_root(event) or event.thread in self.parents

    def _discover(self, event: Notification) -> bool:
        child = event.spawned_thread
        if child is None or child == self.root or child in self.parents or not self.admits(event):
            return False
        self.parents[child] = event.thread
        return True

    def _resolve_pending(self) -> None:
        # A descendant edge can arrive before either its own events or its parent's edge.
        while any(self._discover(event) for event in self.pending):
            pass

    def flush(self) -> list[Notification]:
        accepted = [event for event in self.pending if self.admits(event)]
        self.pending.clear()
        self.pending_bytes = 0
        self.deadline = None
        return accepted

    def offer(self, event: Notification, now: float) -> list[Notification]:
        expired = self.flush() if self.deadline is not None and now >= self.deadline else []
        return expired + self._offer(event, now)

    def _offer(self, event: Notification, now: float) -> list[Notification]:
        if event.thread == self.root and not self.is_root(event):
            return []
        if not self.is_root(event) and event.method not in OBSERVATIONS:
            return []
        if self.deadline is None and self.admits(event):
            self._discover(event)
            return [event]
        return self._enqueue(event, now)

    def _enqueue(self, event: Notification, now: float) -> list[Notification]:
        size = len(json.dumps(event.params, ensure_ascii=True))
        if self.pending_bytes + size > MAX_PENDING_BYTES:
            return self.flush() + self.offer_unqueued(event)
        self.pending.append(event)
        self.pending_bytes += size
        if self.deadline is None:
            self.deadline = now + PENDING_SECONDS
        self._resolve_pending()
        full = len(self.pending) >= MAX_PENDING_COUNT or self.pending_bytes >= MAX_PENDING_BYTES
        if full or all(self.admits(entry) for entry in self.pending) or self.is_terminal(event):
            return self.flush()
        return []

    def offer_unqueued(self, event: Notification) -> list[Notification]:
        if not self.admits(event):
            return []
        self._discover(event)
        return [event]

    def is_terminal(self, event: Notification) -> bool:
        return self.is_root(event) and event.method == "turn/completed"

    def unique(self, record: Json) -> bool:
        identity = sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).digest()
        if identity in self.seen:
            return False
        self.seen.add(identity)
        return True

    def ordinal(self, thread: str) -> int:
        return self.ordinals.setdefault(thread, len(self.ordinals) + 1)

    def incomplete(self) -> list[Json]:
        return [
            {"type": "delegation.incomplete", "thread_id": thread, "parent_thread_id": parent}
            for thread, parent in self.parents.items() if thread not in self.finished
        ]
