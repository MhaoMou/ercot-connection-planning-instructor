from __future__ import annotations

import importlib.util
import json
import shutil
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


def _copy_tutorial(tmp_path: Path) -> Path:
    root = tmp_path / "student_tutorial"
    shutil.copytree(
        TUTORIAL_ROOT,
        root,
        ignore=shutil.ignore_patterns("build", "generated", "outputs"),
    )
    return root


def _write_registry(root: Path, mutate) -> None:
    path = root / "prompt_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    mutate(registry)
    path.write_text(json.dumps(registry), encoding="utf-8")


def test_audit_passes_for_the_checked_in_tutorial():
    auditor = _load_auditor()

    errors = auditor.audit(TUTORIAL_ROOT, allow_unbuilt=True)

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


def test_historical_context_does_not_hide_a_current_legacy_field(tmp_path):
    auditor = _load_auditor()
    (tmp_path / "README.md").write_text(
        "The current seven-column site CSV is authoritative.\n"
        "Historical context; current field: intervention.\n",
        encoding="utf-8",
    )

    errors = auditor.audit(tmp_path)

    assert any("intervention field" in error for error in errors)


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


def test_audit_rejects_registry_path_traversal(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    _write_registry(root, lambda registry: registry["prompts"][0].update(prompt_file="../README.md"))

    errors = auditor.audit(root)

    assert any("outside tutorial root" in error for error in errors)


def test_audit_rejects_registry_symlink_escape(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text((root / "prompts" / "01_assignment_overview.md").read_text(), encoding="utf-8")
    prompt = root / "prompts" / "01_assignment_overview.md"
    prompt.unlink()
    prompt.symlink_to(outside)

    errors = auditor.audit(root)

    assert any("symlink" in error or "outside tutorial root" in error for error in errors)


def test_audit_reports_malformed_and_non_object_json_without_tracebacks(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    (root / "prompt_registry.json").write_text("{", encoding="utf-8")
    errors = auditor.audit(root)
    assert any("registry" in error and "JSON" in error for error in errors)

    root = _copy_tutorial(tmp_path / "non_object")
    (root / "transcripts" / "01_assignment_overview.json").write_text("[]", encoding="utf-8")
    errors = auditor.audit(root)
    assert any("transcript" in error and "object" in error for error in errors)


def test_audit_rejects_null_transcript_fields(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    transcript_path = root / "transcripts" / "01_assignment_overview.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["response"] = None
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    errors = auditor.audit(root)

    assert any("response" in error and "string" in error for error in errors)


def test_audit_rejects_duplicate_registry_paths(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    _write_registry(
        root,
        lambda registry: registry["prompts"][1].update(
            prompt_file=registry["prompts"][0]["prompt_file"]
        ),
    )

    errors = auditor.audit(root)

    assert any("duplicate" in error and "prompt_file" in error for error in errors)


def test_audit_rejects_missing_required_source(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    _write_registry(
        root,
        lambda registry: registry["prompts"][0]["required_sources"].append("docs/missing.md"),
    )

    errors = auditor.audit(root)

    assert any("required source" in error and "missing.md" in error for error in errors)


def test_audit_rejects_unknown_citation_key(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    main = root / "main.tex"
    main.write_text(main.read_text(encoding="utf-8").replace(r"\cite{activsg2000}", r"\cite{missing_key}"), encoding="utf-8")

    errors = auditor.audit(root)

    assert any("bibliography" in error and "missing_key" in error for error in errors)


def test_audit_requires_required_sources_in_transcript_coverage(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    transcript_path = root / "transcripts" / "01_assignment_overview.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["sources"] = ["README.md"]
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    errors = auditor.audit(root)

    assert any("source coverage" in error for error in errors)


def test_audit_requires_rendered_figure_integrations(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    main = root / "main.tex"
    main.write_text(main.read_text(encoding="utf-8").replace(r"\input{figures/storage_soc.tex}", ""), encoding="utf-8")

    errors = auditor.audit(root, allow_unbuilt=True)

    assert any("does not integrate figure storage_soc.tex" in error for error in errors)


def test_audit_ignores_commented_figure_integrations(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    main_path = root / "main.tex"
    main = main_path.read_text(encoding="utf-8")
    main += (
        "\n% \\begin{figure}\n"
        "%   \\input{figures/storage_soc.tex}\n"
        "%   \\caption{Commented-out caption}\n"
        "% \\end{figure}\n"
    )
    active_storage = r"  \input{figures/storage_soc.tex}"
    assert active_storage in main
    main = main.replace(active_storage, "% " + active_storage, 1)
    main_path.write_text(main, encoding="utf-8")

    errors = auditor.audit(root, allow_unbuilt=True)

    assert any("does not integrate figure storage_soc.tex" in error for error in errors)
    assert any("storage_soc.tex must have an integrated caption" in error for error in errors)


def test_release_audit_requires_generated_transcripts(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)

    errors = auditor.audit(root)

    assert any("generated/transcripts.tex" in error for error in errors)


def test_audit_rejects_generated_transcripts_symlink_escape(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    generated = root / "generated"
    generated.mkdir()
    outside = tmp_path / "outside.tex"
    outside.write_text("% outside\n", encoding="utf-8")
    (generated / "transcripts.tex").symlink_to(outside)

    errors = auditor.audit(root)

    assert any("generated/transcripts.tex" in error and "outside" in error for error in errors)


def test_audit_rejects_prompt_registry_symlink_escape(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text((root / "prompt_registry.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prompt_registry.json").unlink()
    (root / "prompt_registry.json").symlink_to(outside)

    errors = auditor.audit(root)

    assert any("registry" in error and "outside tutorial root" in error for error in errors)


def test_audit_ignores_legacy_terms_in_external_historical_docs_but_checks_tutorial(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    historical = root.parent / "docs" / "historical"
    historical.mkdir(parents=True)
    (historical / "old_manual.md").write_text(
        "The legacy intervention and six-period fields were retired.\n",
        encoding="utf-8",
    )
    tutorial_readme = root / "README.md"
    tutorial_readme.write_text(
        tutorial_readme.read_text(encoding="utf-8")
        + "\nCurrent field: intervention\n",
        encoding="utf-8",
    )

    errors = auditor.audit(root)

    assert any("README.md" in error and "intervention" in error for error in errors)
    assert not any("old_manual" in error for error in errors)


def test_audit_rejects_explicit_private_identifiers_but_not_synthetic_fixture_names(tmp_path):
    auditor = _load_auditor()
    root = _copy_tutorial(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "\nteam name: Falcon, plan_id=PLAN-123456, ranking: 1, "
        + "instructor-only diagnostic: objective=42\n",
        encoding="utf-8",
    )

    errors = auditor.audit(root)

    assert any("privacy" in error for error in errors)
