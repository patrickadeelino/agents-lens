import ast
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[2] / "src"


def imported_modules(folder: str) -> list[tuple[str, str]]:
    paths = list((SOURCES / folder).rglob("*.py"))
    assert paths, folder
    return [
        (path.name, node.module)
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    ]


@pytest.mark.parametrize(("folder", "forbidden"), [
    ("backend/domain", ("src.backend.application", "src.backend.infrastructure", "src.backend.api")),
    ("backend/application", ("src.backend.infrastructure", "src.backend.api")),
    # The two services meet only through the shared package.
    ("backend", ("src.executor",)),
    ("executor", ("src.backend",)),
    ("shared", ("src.backend", "src.executor")),
])
def test_imports_respect_service_and_layer_boundaries(folder: str, forbidden: tuple[str, ...]) -> None:
    assert [found for found in imported_modules(folder) if found[1].startswith(forbidden)] == []
