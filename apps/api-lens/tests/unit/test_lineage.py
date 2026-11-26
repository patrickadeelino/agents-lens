from dataclasses import dataclass, field
from typing import Self

import pytest

from src.domain.lineage import InvalidLineage, ancestors, descendants


@dataclass
class Run:
    run_id: str
    saved: dict[str, "Run"] = field(repr=False)
    earlier: str | None = None
    later: str | None = None

    def previous_run_id(self) -> str | None:
        return self.earlier

    def previous(self) -> Self | None:
        return None if self.earlier is None else self.saved[self.earlier]  # type: ignore[return-value]

    def successor(self) -> Self | None:
        return None if self.later is None else self.saved[self.later]  # type: ignore[return-value]


def runs(*links: tuple[str, str | None, str | None]) -> dict[str, Run]:
    saved: dict[str, Run] = {}
    saved.update({name: Run(name, saved, earlier, later) for name, earlier, later in links})
    return saved


def ids(chain) -> list[str]:
    return [run.run_id for run in chain]


def test_a_chain_is_walked_in_both_directions() -> None:
    saved = runs(("a", None, "b"), ("b", "a", "c"), ("c", "b", None))
    assert ids(descendants(saved["a"])) == ["a", "b", "c"]
    assert ids(ancestors(saved["c"])) == ["c", "b", "a"]


@pytest.mark.parametrize("links", [
    (("a", None, "b"), ("b", None, None)),
    (("a", "b", "b"), ("b", "a", "a")),
])
def test_forward_links_must_agree_and_not_repeat(links) -> None:
    with pytest.raises(InvalidLineage):
        list(descendants(runs(*links)["a"]))


@pytest.mark.parametrize("links", [
    (("a", None, None), ("b", "a", None)),
    (("a", "b", "b"), ("b", "a", "a")),
])
def test_backward_links_must_agree_and_not_repeat(links) -> None:
    with pytest.raises(InvalidLineage):
        list(ancestors(runs(*links)["b"]))
