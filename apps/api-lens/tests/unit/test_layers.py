import ast
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[2] / "src"
OUTER_LAYERS = ("src.api", "src.infrastructure")


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_do_not_import_outer_layers(layer: str) -> None:
    imported = [
        (path.name, node.module)
        for path in (SOURCES / layer).glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(OUTER_LAYERS)
    ]
    assert imported == []
