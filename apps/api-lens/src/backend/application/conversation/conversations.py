"""Project conversation summaries from accepted capture lineage."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.backend.application.conversation.conversation_ports import (
    ConversationRepository,
    ConversationResult,
    ConversationRun,
)
from src.backend.application.conversation.run_continuation import (
    ContinuationInfo,
    RunSession,
    describe_continuation,
)
from src.backend.domain.conversation.lineage import ancestors, descendants
from src.backend.domain.turn.turn_outcome import turn_status
from src.backend.domain.turn.turn_summary import TurnSummary
from src.shared.executor_protocol import AgentName
from src.shared.observability import event_log


@dataclass(frozen=True)
class Turn:
    run_id: str
    prompt: str
    assistant_markdown: str | None
    submitted_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    status: str
    summary: TurnSummary | None = None


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: UUID | None
    root_run_id: str
    latest_run_id: str
    title: str
    preview: str
    last_activity_at: datetime
    status: str
    workspace: str
    agent: AgentName


@dataclass(frozen=True)
class ThreadDetail(ThreadSummary):
    turns: list[Turn]
    continuation: ContinuationInfo


def preview(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


@dataclass(frozen=True)
class ThreadList:
    threads: list[ThreadSummary]
    unavailable: int


@dataclass(frozen=True)
class _Scan:
    threads: list[ThreadDetail]
    unavailable: int
    duplicated: set[UUID]


def turn_state(capture: ConversationRun, result: ConversationResult | None, last_turn: str | None) -> str:
    if capture.is_active():
        return "active"
    if result is None:
        return "unconfirmed"
    return turn_status(last_turn, result.exit_code, capture_failed=result.capture_failed)


class Conversations[Run: ConversationRun]:
    def __init__(self, store: ConversationRepository[Run]) -> None:
        self.store = store

    def _turn(self, capture: Run, session: RunSession | None, result: ConversationResult | None, *,
              details: bool) -> Turn:
        if session is None:
            raise ValueError("Conversation turn has no session.")
        status = turn_state(capture, result, result.last_turn if result else None)
        return Turn(capture.run_id, session.prompt, result.assistant_markdown if result else None,
                    session.submitted_at, result.finished_at if result else None,
                    result.exit_code if result else None, status, result.summary if result and details else None)

    def _project(self, root: Run, *, details: bool) -> ThreadDetail | None:
        session = root.read_session()
        result = root.read_result()
        if session is None:
            return None
        thread_id = result.thread_id if result else None
        if result is None and not root.is_active() and not root.has_trace():
            return None
        chain = list(descendants(root))
        # Each file is read once, so every field below describes the same moment of a run.
        sessions = [session, *(child.read_session() for child in chain[1:])]
        results = [result, *(child.read_result() for child in chain[1:])]
        for child_result in results[1:]:
            if child_result and (child_result.conflicting_thread_id is not None or
                                 child_result.thread_id not in (None, thread_id)):
                raise ValueError("Conflicting conversation identity.")
        if any(child and child.agent != session.agent for child in sessions):
            raise ValueError("Conflicting conversation agent.")
        turns = [self._turn(*saved, details=details) for saved in zip(chain, sessions, results, strict=True)]
        dates = [date for turn in turns for date in (turn.submitted_at, turn.finished_at) if date is not None]
        latest = turns[-1]
        continuation = describe_continuation(latest.run_id, chain[-1], results[-1])
        return ThreadDetail(thread_id, turns[0].run_id, latest.run_id,
                            preview(turns[0].prompt, 80), preview(latest.prompt, 140), max(dates),
                            latest.status, session.workspace, session.agent, turns, continuation)

    def _scan(self) -> _Scan:
        """Project every conversation, leaving out the damaged ones without failing the others."""
        roots, unavailable = self.store.roots()
        threads = []
        for root in roots:
            try:
                thread = self._project(root, details=False)
            except (OSError, ValueError):
                event_log("conversation.skipped", "history", "failed", error_code="capture_unreadable")
                unavailable += 1
                continue
            if thread:
                threads.append(thread)
        counts = Counter(thread.thread_id for thread in threads if thread.thread_id is not None)
        duplicated = {thread_id for thread_id, total in counts.items() if total > 1}
        readable = [thread for thread in threads if thread.thread_id not in duplicated]
        return _Scan(readable, unavailable + len(threads) - len(readable), duplicated)

    def list(self) -> ThreadList:
        scan = self._scan()
        threads = scan.threads
        threads.sort(key=lambda thread: thread.root_run_id)
        threads.sort(key=lambda thread: thread.last_activity_at.timestamp(), reverse=True)
        return ThreadList([ThreadSummary(t.thread_id, t.root_run_id, t.latest_run_id, t.title, t.preview,
                                         t.last_activity_at, t.status, t.workspace, t.agent) for t in threads],
                          scan.unavailable)

    def by_run(self, run_id: str) -> ThreadDetail | None:
        capture = self.store.find(run_id)
        *_, root = ancestors(capture)
        detail = self._project(root, details=True)
        if detail and not any(turn.run_id == run_id for turn in detail.turns):
            raise ValueError("Invalid conversation lineage.")
        if detail and detail.thread_id is not None and detail.thread_id in self._scan().duplicated:
            raise ValueError("Duplicate conversation identity.")
        return detail

    def by_thread(self, thread_id: UUID) -> ThreadDetail | None:
        scan = self._scan()
        for thread in scan.threads:
            if thread.thread_id == thread_id:
                # The scan already ruled out duplicates; only the turn details are missing.
                return self._project(self.store.find(thread.root_run_id), details=True)
        # With damaged captures, a missing thread cannot be told apart from an unreadable one.
        if scan.unavailable:
            raise ValueError("Conversation may be unreadable.")
        return None
