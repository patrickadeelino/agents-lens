"""Remember the folders a person chose, for as long as this executor runs."""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4


class WorkspaceSelections:
    """Keep the latest selections in memory, dropping the oldest at the limit."""

    def __init__(self, *, limit: int = 128, new_id: Callable[[], UUID] = uuid4) -> None:
        self._paths: dict[UUID, Path] = {}
        self._limit = limit
        self._new_id = new_id

    def remember(self, path: Path) -> UUID:
        if len(self._paths) >= self._limit:
            del self._paths[next(iter(self._paths))]
        selection = self._new_id()
        self._paths[selection] = path
        return selection

    def find(self, selection: UUID) -> Path | None:
        return self._paths.get(selection)
