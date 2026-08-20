from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def safe_payload() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "fixtures" / "safe-tool-trace.otlp.json"
    return json.loads(path.read_text(encoding="utf-8"))
