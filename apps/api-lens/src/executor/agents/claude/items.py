"""Describe a Claude Code tool call as an item in the capture's own format."""

import re
from typing import Any

from src.executor.agents.base import Json

_EXIT_CODE = re.compile(r"Exit code (\d+)(?:\n|$)")
_FILE_TOOLS = {"Edit", "Write"}


def started_item(call: Json) -> Json:
    name, arguments = call["name"], call.get("input") or {}
    item: Json = {"id": call["id"], "status": "in_progress"}
    if name == "Bash":
        return item | {
            "type": "command_execution", "command": arguments.get("command"),
            "aggregated_output": "", "exit_code": None,
        }
    if name in _FILE_TOOLS:
        change = {"path": arguments.get("file_path"), "kind": "update" if name == "Edit" else None}
        return item | {"type": "file_change", "changes": [change]}
    return item | {"type": "tool_call", "tool": name, "arguments": arguments}


def completed_item(started: Json, answer: Json, detail: Any, *, denied: bool) -> Json:
    """Add the outcome to a started item. `detail` is the structured result, when Claude Code sends one."""
    failed = answer.get("is_error") is True
    status = "declined" if denied else "failed" if failed else "completed"
    item = started | {"status": status}
    text = result_text(answer.get("content"))
    if item["type"] == "command_execution":
        return item | {"aggregated_output": text, "exit_code": exit_code(text) if status == "failed" else None}
    if item["type"] == "file_change":
        return item | {"changes": confirmed_changes(item["changes"], detail)}
    if status == "completed":
        return item | {"result": text}
    return item | {"error": {"message": text}}


def exit_code(output: str) -> int | None:
    """Claude Code has no exit code field: only a failed command states it, in the first line of its output."""
    stated = _EXIT_CODE.match(output)
    return int(stated[1]) if stated else None


def confirmed_changes(changes: list[Json], detail: Any) -> list[Json]:
    """Only the structured result tells whether Write created a file or replaced one."""
    reported = detail.get("type") if isinstance(detail, dict) else None
    kind = {"create": "add", "update": "update"}.get(str(reported))
    return changes if kind is None else [change | {"kind": kind} for change in changes]


def result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block["text"] for block in content if block.get("type") == "text")
