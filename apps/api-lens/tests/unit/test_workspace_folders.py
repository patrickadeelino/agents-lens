from pathlib import Path

import pytest

from src.infrastructure.workspace_folders import (
    FolderUnavailable,
    list_folders,
    resolve_folder,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path.resolve() / "root"
    for name in ("beta", "Alpha", ".hidden"):
        (base / name).mkdir(parents=True)
    (base / "notes.txt").write_text("file", encoding="utf-8")
    outside = base.parent / "outside"
    outside.mkdir()
    (base / "escape").symlink_to(outside, target_is_directory=True)
    return base


def test_lists_only_visible_real_subfolders(root: Path) -> None:
    assert list_folders(root) == (["Alpha", "beta"], False)


def test_resolves_the_root_and_its_subfolders(root: Path) -> None:
    assert resolve_folder(root, "") == root
    assert resolve_folder(root, "beta") == root / "beta"


@pytest.mark.parametrize("path", [
    "..", "beta/../..", "/", "/etc", "escape", ".hidden", "missing", "notes.txt", "bad\0name",
])
def test_refuses_paths_outside_the_visible_tree(root: Path, path: str) -> None:
    with pytest.raises(ValueError):
        resolve_folder(root, path)


def test_reports_a_folder_that_disappeared(root: Path) -> None:
    with pytest.raises(FolderUnavailable):
        list_folders(root / "missing")
