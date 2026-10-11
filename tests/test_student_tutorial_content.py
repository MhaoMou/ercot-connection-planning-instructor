from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_ROOT = REPO_ROOT / "docs" / "student_tutorial"
AUDITOR_PATH = TUTORIAL_ROOT / "scripts" / "audit_tutorial_content.py"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("audit_tutorial_content", AUDITOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audit_passes_for_the_checked_in_tutorial():
    auditor = _load_auditor()

    errors = auditor.audit(TUTORIAL_ROOT)

    assert errors == []


def test_legacy_terms_are_allowed_only_as_explicit_rejections_or_comparisons(tmp_path):
    auditor = _load_auditor()
    (tmp_path / "README.md").write_text(
        "The current seven-column site CSV is authoritative.\n"
        "The retired `period` and `load_mw` fields are rejected as current columns.\n"
        "Historical six-period plans are not part of this tutorial.\n",
        encoding="utf-8",
    )
    (tmp_path / "main.tex").write_text(
        "AI-use and privacy guidance. one-hour half-year scenarios.\n"
        "4,368 hours for 2028 January--June and 4,416 hours for July--December.\n"
        "The current CSV header is team_id,site_id,bus_id,connection_capacity_mw,"
        "generator_capacity_mw,storage_power_mw,storage_energy_mwh.\n"
        "Source: README.md; \\cite{source}.\n"
        "The legacy intervention field is rejected; path corridor is historical only.\n",
        encoding="utf-8",
    )
    (tmp_path / "references.bib").write_text("@misc{source, title={Source}}", encoding="utf-8")
    (tmp_path / "prompt_registry.json").write_text(
        '{"schema_version": 1, "prompts": []}', encoding="utf-8"
    )

    errors = auditor.audit(tmp_path)

    assert not any("legacy" in error or "six-period" in error for error in errors)


def test_audit_rejects_current_legacy_field_claims_and_private_paths(tmp_path):
    auditor = _load_auditor()
    (tmp_path / "README.md").write_text(
        "Current fields: period, load_mw, intervention, curtailment, path corridor.\n"
        "Contact token=ghp_1234567890abcdefghijklmnop.\n"
        "Private file: /Users/alice/team-plan.csv\n",
        encoding="utf-8",
    )

    errors = auditor.audit(tmp_path)

    assert any("legacy" in error or "current submission" in error for error in errors)
    assert any("private" in error or "secret" in error for error in errors)
