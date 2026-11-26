import ast
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[2] / "src"
# Counted from the def line to the last line of the body, without decorators.
MAX_LINES = 30


def test_production_functions_stay_short() -> None:
    paths = list(SOURCES.rglob("*.py"))
    assert paths
    too_long = [
        f"{path.relative_to(SOURCES)}:{node.lineno} {node.name} has {node.end_lineno - node.lineno + 1} lines"
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno is not None and node.end_lineno - node.lineno + 1 > MAX_LINES
    ]
    assert too_long == []
