"""Project compact evidence from records observed during a single capture."""

import re
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field

from .command_execution import CommandItem
from .trace_events import TimedEvent

_TOOLS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"}
_FAILED = {"failed", "declined"}
_TEST = re.compile(r"^(?:(?:rtk(?: proxy)?|uv run)\s+)*(?:pytest|python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest))(?:\s|$)")


class ChangedFile(BaseModel):
    path: str
    kind: str | None = None
    item_id: str


class TestExecution(BaseModel):
    item_id: str
    status: str
    exit_code: int | None = None


class TurnSummary(BaseModel):
    actions: int | None = None
    failures: int | None = None
    partial: bool = False
    tokens: dict[str, int]
    observed_duration_ns: int | None = None
    total_duration_ns: int | None = Field(default=None, ge=0)
    failure_item_ids: list[str] = Field(default_factory=list)
    files: list[ChangedFile]
    tests: list[TestExecution]


def _changes(item: dict[str, Any], item_id: str) -> list[ChangedFile]:
    changes = item.get("changes")
    if not isinstance(changes, list) or item.get("status") != "completed":
        return []
    return [
        ChangedFile(path=change["path"], kind=change.get("kind") if isinstance(change.get("kind"), str) else None,
                    item_id=item_id)
        for change in changes if isinstance(change, dict) and isinstance(change.get("path"), str) and change["path"]
    ]


class TurnEvidence:
    def __init__(self) -> None:
        self._offsets: list[int] = []
        self._actions: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self._terminal: dict[tuple[str, str], bytes] = {}
        self._conflicts: set[tuple[str, str]] = set()
        self._files: dict[tuple[str, str], list[ChangedFile]] = {}
        self._tests: dict[tuple[str, str], TestExecution] = {}
        self._tokens: dict[str, int] = {}
        self._unidentified = False

    def observe(self, record: TimedEvent) -> None:
        if len(self._offsets) < 2:
            self._offsets.append(record.elapsed_ns)
        else:
            self._offsets[-1] = record.elapsed_ns
        event = record.event
        extra = event.model_extra or {}
        if event.type == "turn.completed":
            self._observe_usage(extra.get("usage"))
        item = extra.get("item")
        if isinstance(item, dict) and event.type in {"item.started", "item.updated", "item.completed"}:
            self._observe_action(record, item)

    def _observe_usage(self, usage: object) -> None:
        if isinstance(usage, dict):
            self._tokens = {key: value for key, value in usage.items()
                            if key in {"input_tokens", "output_tokens", "cached_input_tokens",
                                       "cache_write_input_tokens", "reasoning_output_tokens"}
                            and type(value) is int and value >= 0}

    def _observe_action(self, record: TimedEvent, item: dict[str, Any]) -> None:
        kind, item_id = item.get("type"), item.get("id")
        if not isinstance(kind, str) or kind not in _TOOLS:
            return
        if not isinstance(item_id, str) or not item_id:
            self._unidentified = True
            return
        key = (kind, item_id)
        self._actions.add(key)
        if record.event.type == "item.completed":
            self._observe_terminal(record, item, key)

    def _observe_terminal(self, record: TimedEvent, item: dict[str, Any], key: tuple[str, str]) -> None:
        status = item.get("status")
        if isinstance(status, str) and status in _FAILED:
            self._failed.add(key)
        fingerprint = sha256(record.event.model_dump_json().encode()).digest()
        if self._terminal.setdefault(key, fingerprint) != fingerprint:
            self._conflicts.add(key)
        if key[0] == "file_change":
            self._files[key] = _changes(item, key[1])
        elif key[0] == "command_execution":
            self._observe_test(record, key)

    def _observe_test(self, record: TimedEvent, key: tuple[str, str]) -> None:
        command = CommandItem.from_event(record.event)
        if command is None or not command.command or not command.status:
            return
        shell = re.match(r"^(?:\S*/)?(?:sh|bash|zsh)\s+-[a-z]*c\s+['\"]?(.*)", command.command)
        candidate = (shell[1] if shell else command.command).rstrip().rstrip("'\"")
        if _TEST.match(candidate) and not re.search(r'[;&|<>\n`$]', candidate):
            self._tests[key] = TestExecution(item_id=key[1], status=command.status, exit_code=command.exit_code)

    def finish(self, *, interrupted: bool, total_duration_ns: int | None = None) -> TurnSummary:
        partial = interrupted or self._unidentified
        files = {file.path: file for key, changes in self._files.items() if key not in self._conflicts for file in changes}
        return TurnSummary(
            actions=len(self._actions) if self._actions or (self._offsets and not partial) else None,
            failures=len(self._failed) if self._offsets and (self._failed or not partial) else None,
            partial=partial, tokens=self._tokens, total_duration_ns=total_duration_ns,
            observed_duration_ns=self._offsets[-1] - self._offsets[0] if len(self._offsets) == 2 else None,
            failure_item_ids=list(dict.fromkeys(item_id for kind, item_id in self._terminal if (kind, item_id) in self._failed)),
            files=list(files.values()), tests=[test for key, test in self._tests.items() if key not in self._conflicts],
        )
