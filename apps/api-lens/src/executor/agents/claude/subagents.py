"""Follow the subagents of a run and describe them with the capture's delegation events."""

from dataclasses import dataclass

from src.executor.agents.base import Json

# Current and former names of the tool that starts a subagent.
SUBAGENT_TOOLS = {"Agent", "Task"}


@dataclass
class Subagent:
    """A requested subagent. It exists only once the run shows it started."""

    parent: str | None
    name: str | None
    ordinal: int | None = None
    ended: bool = False


class Subagents:
    """Each subagent is identified by the tool call that asked for it."""

    def __init__(self) -> None:
        self.root = ""
        self._requested: dict[str, Subagent] = {}
        self._started = 0

    def request(self, call: Json, parent: str | None) -> None:
        if call["name"] in SUBAGENT_TOOLS:
            name = (call.get("input") or {}).get("subagent_type")
            self._requested[call["id"]] = Subagent(parent, name)

    def known(self, call: str) -> bool:
        return call in self._requested

    def start(self, call: str) -> list[Json]:
        """Announce the subagent the first time it shows itself."""
        child = self._requested.get(call)
        if child is None or child.ordinal is not None:
            return []
        self._started += 1
        child.ordinal = self._started
        parent = child.parent or self.root
        edge = {
            "type": "delegation.edge", "parent_thread_id": parent, "parent_turn_id": parent,
            "item_id": call, "child_thread_id": call, "child_index": child.ordinal,
        }
        return [edge, observation(call, {"type": "turn.started"})]

    def end(self, call: str, status: str) -> list[Json]:
        child = self._requested.get(call)
        if child is None or child.ordinal is None or child.ended:
            return []
        child.ended = True
        events = [observation(call, {"type": "turn.completed", "status": status})]
        if status == "completed" and child.name:
            # The interface takes the subagent's name from this observation.
            named = {"type": "subagent.completed", "item": {"agentPath": child.name}}
            events.append(observation(call, named))
        return events

    def incomplete(self) -> list[Json]:
        return [
            {"type": "delegation.incomplete", "thread_id": call, "parent_thread_id": child.parent or self.root}
            for call, child in self._requested.items() if child.ordinal is not None and not child.ended
        ]


def observation(call: str, event: Json) -> Json:
    """Wrap what a subagent did. Each one runs a single turn, named after it."""
    return {
        "type": "delegation.event", "thread_id": call, "turn_id": call,
        "item_id": (event.get("item") or {}).get("id"), "observation": event,
    }


def notified_status(reported: object) -> str:
    """Only a reported completion counts as success."""
    if reported in {"completed", "failed"}:
        return str(reported)
    return "interrupted"


def answered_status(answer: Json, detail: object) -> str | None:
    """Return how a call's own result ended its subagent, or None when it did not say."""
    if answer.get("is_error") is True:
        return "failed"
    if isinstance(detail, dict) and detail.get("status") == "completed":
        return "completed"
    return None
