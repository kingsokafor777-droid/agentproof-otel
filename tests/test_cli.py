from __future__ import annotations

import json
from pathlib import Path

from agentproof_otel.cli import main


def test_normalize_command_emits_core_trace(capsys, tmp_path: Path) -> None:
    payload_path = Path(__file__).parents[1] / "fixtures" / "safe-tool-trace.otlp.json"
    path = tmp_path / "trace.json"
    path.write_text(payload_path.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["normalize", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["traces"][0]["source"] == "agentproof.otel"
    assert len(output["traces"][0]["events"]) == 2


def test_normalize_command_reports_invalid_input(capsys, tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not-json", encoding="utf-8")

    assert main(["normalize", str(path)]) == 2
    assert "INVALID:" in capsys.readouterr().err
