"""Load the bundled fictional recordings as saved conversations, for the demo without an API key."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from src.backend.application.conversation.run_continuation import RunSession
from src.backend.domain.turn.turn_outcome import agent_reply
from src.backend.domain.turn.turn_summary import TurnEvidence
from src.backend.infrastructure.storage.capture_files import CaptureFiles
from src.shared.trace_events import TimedEvent, TraceEvent

PROJECT = Path(__file__).resolve().parents[3]
RECORDINGS = PROJECT / "demo" / "recordings.json"
CAPTURE_ROOT = PROJECT.parents[1] / ".captures"


class Turn(BaseModel):
    prompt: str
    events: list[tuple[int, TraceEvent]]


class Conversation(BaseModel):
    folder: str
    thread_id: UUID
    age_minutes: int
    turns: list[Turn]


def save_turn(capture: CaptureFiles, conversation: Conversation, turn: Turn, finished_at: datetime) -> None:
    """Write the trace and result the same way a live capture leaves them, with the recorded timing."""
    evidence = TurnEvidence()
    reply = None
    with capture.open_trace() as trace:
        for offset_ms, event in turn.events:
            record = TimedEvent(elapsed_ns=offset_ms * 1_000_000, event=event)
            evidence.observe(record)
            trace.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
            reply = agent_reply(event) or reply
    duration_ns = turn.events[-1][0] * 1_000_000
    capture.write_result(
        0, thread_id=conversation.thread_id, assistant_markdown=reply,
        last_turn=turn.events[-1][1].type, finished_at=finished_at,
        summary=evidence.finish(interrupted=False, total_duration_ns=duration_ns),
    )


def save_conversation(root: Path, conversation: Conversation, now: datetime) -> None:
    previous = None
    for index, turn in enumerate(conversation.turns):
        submitted_at = now - timedelta(minutes=conversation.age_minutes - index * 3)
        capture = CaptureFiles.create(root, prefix="codex-prompt-")
        capture.write_session(RunSession(
            previous_run_id=previous.directory.name if previous else None,
            prompt=turn.prompt, submitted_at=submitted_at, workspace=conversation.folder,
        ))
        if previous:
            previous.reserve_successor(capture)
        save_turn(capture, conversation, turn, submitted_at + timedelta(milliseconds=turn.events[-1][0]))
        previous = capture


def load_demo(root: Path, recordings: Path = RECORDINGS) -> int:
    """Seed an empty capture directory and return how many conversations were added."""
    if root.exists() and any(root.iterdir()):
        return 0
    data = json.loads(recordings.read_text(encoding="utf-8"))
    conversations = [Conversation.model_validate(entry) for entry in data]
    now = datetime.now(UTC)
    for conversation in conversations:
        save_conversation(root, conversation, now)
    return len(conversations)


if __name__ == "__main__":
    print(f"Demo conversations added: {load_demo(CAPTURE_ROOT)}", flush=True)
