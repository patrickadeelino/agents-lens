"""Traverse capture lineage without following filesystem links."""

from pathlib import Path

from src.infrastructure.capture_files import CaptureFiles


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def find(self, run_id: str) -> CaptureFiles:
        return CaptureFiles.find(self.root, run_id)

    def roots(self) -> list[CaptureFiles]:
        if not self.root.exists():
            return []
        captures = []
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir() or not path.name.startswith("codex-prompt-"):
                continue
            capture = self.find(path.name)
            session = capture.read_session()
            if session is None or session.previous_run_id is None:
                captures.append(capture)
        return captures

    def lineage(self, root: CaptureFiles) -> list[CaptureFiles]:
        chain = [root]
        visited = {root.directory.name}
        while (child := chain[-1].successor()) is not None:
            child_id = child.directory.name
            session = child.read_session()
            if child_id in visited or session is None or session.previous_run_id != chain[-1].directory.name:
                raise ValueError("Invalid conversation lineage.")
            visited.add(child_id)
            chain.append(child)
        return chain

    def root_for(self, capture: CaptureFiles) -> CaptureFiles:
        visited: set[str] = set()
        while True:
            run_id = capture.directory.name
            if run_id in visited:
                raise ValueError("Invalid conversation lineage.")
            visited.add(run_id)
            session = capture.read_session()
            if session is None or session.previous_run_id is None:
                return capture
            try:
                parent = self.find(session.previous_run_id)
            except FileNotFoundError as error:
                raise ValueError("Invalid conversation lineage.") from error
            child = parent.successor()
            if child is None or child.directory != capture.directory:
                raise ValueError("Invalid conversation lineage.")
            capture = parent
