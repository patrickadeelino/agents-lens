"""List and resolve the folders a person may choose below the executor's workspace root."""

import os
from pathlib import Path, PurePosixPath

MAX_FOLDERS = 500


class FolderUnavailable(ValueError):
    """The requested folder does not exist or is not allowed."""


def resolve_folder(root: Path, relative: str) -> Path:
    """Return the directory at a root-relative path, refusing hidden folders and escapes from the root."""
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part.startswith(".") for part in parts.parts):
        raise FolderUnavailable("Folder path is not allowed.")
    try:
        resolved = (root / parts).resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise FolderUnavailable("Folder is outside the workspace root.")
    except (OSError, RuntimeError) as error:
        raise FolderUnavailable("Folder is unavailable.") from error
    return resolved


def list_folders(directory: Path) -> tuple[list[str], bool]:
    """Return the visible subfolder names and whether the list was cut short. Links are not followed."""
    try:
        with os.scandir(directory) as entries:
            names = sorted(
                (entry.name for entry in entries if selectable(entry)), key=str.casefold,
            )
    except OSError as error:
        raise FolderUnavailable("Folder is unavailable.") from error
    return names[:MAX_FOLDERS], len(names) > MAX_FOLDERS


def selectable(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
        return False
    # Names that are not valid UTF-8 cannot be sent as JSON.
    return entry.name.encode("utf-8", "replace").decode("utf-8") == entry.name
