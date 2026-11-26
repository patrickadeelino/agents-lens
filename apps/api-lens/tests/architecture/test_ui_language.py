"""Guard the en-US interface: messages sent to the browser or the desktop stay in English."""

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PORTUGUESE = re.compile(
    r"[áàâãéêíóôõúç]|\b(arquivos?|conversas?|executor indisponivel|falha|falhou|indisponivel|inicie|"
    r"nenhuma?|pasta|restaure|selecion[ae]r?|tente|tentar)\b",
    re.IGNORECASE,
)


def test_shipped_text_contains_no_portuguese() -> None:
    sources = [
        PROJECT / "demo" / "recordings.json",
        *sorted((PROJECT / "src").rglob("*.py")),
    ]
    found = [
        f"{path.relative_to(PROJECT)}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if PORTUGUESE.search(line)
    ]
    assert found == []
