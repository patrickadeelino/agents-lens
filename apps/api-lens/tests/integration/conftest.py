from pathlib import Path

import pytest

from src.api.dependencies import CAPTURE_ROOT, PROJECT_DIR


@pytest.fixture
def project_root() -> Path:
    return PROJECT_DIR


@pytest.fixture
def capture_root() -> Path:
    CAPTURE_ROOT.mkdir(mode=0o700, exist_ok=True)
    return CAPTURE_ROOT
