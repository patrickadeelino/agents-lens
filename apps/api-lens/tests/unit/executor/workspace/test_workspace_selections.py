from pathlib import Path
from uuid import uuid4

from src.executor.workspace.workspace_selections import WorkspaceSelections


def test_a_selection_is_found_by_the_id_it_was_given() -> None:
    selections = WorkspaceSelections()
    first = selections.remember(Path("/work/first"))
    second = selections.remember(Path("/work/second"))
    assert (selections.find(first), selections.find(second)) == (Path("/work/first"), Path("/work/second"))
    assert selections.find(uuid4()) is None


def test_the_oldest_selection_is_dropped_at_the_limit() -> None:
    selections = WorkspaceSelections(limit=2)
    chosen = [selections.remember(Path(name)) for name in ("a", "b", "c")]
    assert [selections.find(selection) for selection in chosen] == [None, Path("b"), Path("c")]
