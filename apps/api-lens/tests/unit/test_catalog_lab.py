from pathlib import Path

import pytest

from executor import prepare_catalog_lab


def test_seed_is_copied_only_once_and_layout_is_separate(tmp_path: Path) -> None:
    seed = tmp_path / "examples" / "catalog-api"
    seed.mkdir(parents=True)
    (seed / "catalog.py").write_text("initial")
    workspace, state, socket = prepare_catalog_lab(tmp_path)
    (workspace / "catalog.py").write_text("edited")
    assert prepare_catalog_lab(tmp_path) == (workspace, state, socket)
    assert (workspace / "catalog.py").read_text() == "edited"
    assert workspace.parent == state.parent == socket.parent.parent
    assert socket.parent.stat().st_mode & 0o777 == 0o711


def test_rejects_symlinked_workspace(tmp_path: Path) -> None:
    lab = tmp_path / ".local" / "codex-executor" / "catalog"
    lab.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (lab / "workspace").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        prepare_catalog_lab(tmp_path)
