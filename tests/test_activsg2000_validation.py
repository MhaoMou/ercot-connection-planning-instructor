from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.activsg2000.build_scenario_package import build_package
from tools.activsg2000.validate_scenario_package import validate_package


def test_validator_does_not_mark_partial_validation_as_passed(tmp_path: Path) -> None:
    pytest.importorskip("highspy")
    source = Path("/private/tmp/activsg2000_source/ACTIVSg2000-master")
    if not source.exists():
        pytest.skip("canonical ACTIVSg2000 source is not available")
    package = tmp_path / "package"
    build_package(source, package)
    result = validate_package(package, max_hours=1)
    assert result["validation_status"] == "partial"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation_status"] == "partial"
    assert manifest["validation"]["hour_count"] == 1
