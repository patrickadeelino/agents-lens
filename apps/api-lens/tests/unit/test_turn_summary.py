from src.backend.domain.turn.turn_summary import TurnEvidence
from src.shared.trace_events import TimedEvent, TraceEvent


def test_turn_summary_counts_unique_actions_and_links_observed_evidence() -> None:
    evidence = TurnEvidence()

    def observe(event_type: str, offset: int, **extra: object) -> None:
        evidence.observe(TimedEvent(elapsed_ns=offset, event=TraceEvent(type=event_type, **extra)))

    first = {"id": "test", "type": "command_execution", "command": "python -m unittest", "status": "failed", "exit_code": 1}
    observe("turn.started", 0)
    observe("item.completed", 8, item=first)
    observe("item.started", 9, item={**first, "status": "in_progress"})
    observe("item.completed", 10, item=first)
    observe("item.completed", 11, item={**first, "id": "retry", "status": "completed", "exit_code": 0})
    observe("item.completed", 12, item={
        "id": "edit", "type": "file_change", "status": "completed", "changes": [{"path": "api.py", "kind": "update"}],
    })
    observe("turn.completed", 15, usage={"input_tokens": 3, "output_tokens": 0})
    summary = evidence.finish(interrupted=False)
    assert (summary.actions, summary.failures, summary.observed_duration_ns) == (3, 1, 15)
    assert summary.failure_item_ids == ["test"]
    assert summary.tokens == {"input_tokens": 3, "output_tokens": 0}
    assert [(file.path, file.item_id) for file in summary.files] == [("api.py", "edit")]
    assert [(test.item_id, test.exit_code) for test in summary.tests] == [("test", 1), ("retry", 0)]

    observe("item.completed", 16, item={**first, "status": "completed", "exit_code": 0})
    conflicting = evidence.finish(interrupted=False)
    assert conflicting.failures == 1
    assert [test.item_id for test in conflicting.tests] == ["retry"]


def test_missing_evidence_is_distinct_from_observed_zero() -> None:
    evidence = TurnEvidence()
    assert evidence.finish(interrupted=True).actions is None
    evidence.observe(TimedEvent(elapsed_ns=0, event=TraceEvent(type="turn.started")))
    partial = evidence.finish(interrupted=True)
    assert partial.partial and partial.actions is None and partial.failures is None
    complete = evidence.finish(interrupted=False)
    assert not complete.partial and complete.actions == 0 and complete.failures == 0
