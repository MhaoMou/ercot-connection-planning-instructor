import json
import importlib.util
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "docs" / "student_tutorial" / "prompt_registry.json"
TUTORIAL_VALIDATOR = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "validate_tutorial_examples.py"
TUTORIAL_ROOT = REPO_ROOT / "docs" / "student_tutorial"

REQUIRED_PROMPT_IDS = [
    "assignment-overview",
    "dataset-inspection",
    "python-visualization",
    "matlab-visualization",
    "dcopf-concept",
    "dcopf-formulation",
    "three-bus-implementation",
    "csv-format",
    "storage-model",
    "colab-preview",
    "debugging",
    "strategy-experiments",
    "reproducibility",
]
EXPECTED_PRIVACY_LEVEL = "public-or-instructor-approved-fixture"
EXPECTED_PROMPT_ROOT = "prompts/"
EXPECTED_TRANSCRIPT_ROOT = "transcripts/"
EXPECTED_NOTEBOOK_ROOT = "notebooks/"
REQUIRED_PROMPT_HEADINGS = [
    "# Prompt metadata",
    "## Goal",
    "## Context supplied to the AI",
    "## Prompt",
    "## Expected evidence",
    "## Privacy constraints",
]
TRANSCRIPT_KEYS = {
    "prompt_id",
    "prompt",
    "response",
    "verification_checklist",
    "agent_label",
    "generated_at",
    "review_status",
    "review_notes",
    "sources",
    "private_information_present",
}


def _load_transcript_validator():
    path = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "validate_transcripts.py"
    spec = importlib.util.spec_from_file_location("validate_transcripts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_registry_validator():
    path = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "validate_prompt_registry.py"
    spec = importlib.util.spec_from_file_location("validate_prompt_registry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_tutorial_validator():
    spec = importlib.util.spec_from_file_location("validate_tutorial_examples", TUTORIAL_VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_tutorial_validator_rejects_malformed_strategy_csv_rows(tmp_path):
    validator = _load_tutorial_validator()
    path = tmp_path / "strategy_ablation.csv"
    path.write_text(
        ",".join(validator.CANONICAL_COLUMNS) + "\n"
        "fixture-team,site-1,1001,40,20,-1,not-a-number,extra\n"
        "fixture-team,site-2,1001,40,20,-1,not-a-number\n"
        "fixture-team,site-3,1001,40,20,-1,0\n",
        encoding="utf-8",
    )

    errors = validator.validate_strategy_csv(path)

    assert any("exactly" in error for error in errors)
    assert any("numeric" in error for error in errors)
    assert any("non-negative" in error for error in errors)


def test_tutorial_validator_rejects_retired_hourly_csv_header(tmp_path):
    validator = _load_tutorial_validator()
    path = tmp_path / "strategy_ablation.csv"
    path.write_text(
        "submission_id,team_id,period,site_id,bus_id,generator_id,storage_id,load_mw,generator_capacity_mw,storage_power_mw\n"
        "submission-1,fixture-team,1,site-1,1001,generator-1,storage-1,35,20,5\n",
        encoding="utf-8",
    )

    errors = validator.validate_strategy_csv(path)

    assert any("exact canonical CSV header" in error for error in errors)


def test_tutorial_validator_rejects_blank_strategy_ids_and_required_numbers(tmp_path):
    validator = _load_tutorial_validator()
    path = tmp_path / "strategy_ablation.csv"
    path.write_text(
        ",".join(validator.CANONICAL_COLUMNS) + "\n"
        ",site-1,1001,40,20,10,50\n"
        "fixture-team,   ,1001,40,20,10,50\n"
        "fixture-team,site-3,  ,40,20,10,50\n"
        "fixture-team,site-4,1001,,20,10,50\n"
        "fixture-team,site-5,1001,40,not-a-number,10,50\n"
        "fixture-team,site-6,1001,40,20,10,\n",
        encoding="utf-8",
    )

    errors = validator.validate_strategy_csv(path)

    assert any("team_id" in error and "required" in error for error in errors)
    assert any("site_id" in error and "required" in error for error in errors)
    assert any("bus_id" in error and "required" in error for error in errors)
    assert sum("must be numeric" in error for error in errors) >= 3


def test_tutorial_validator_rejects_unexpected_published_artifacts(tmp_path):
    validator = _load_tutorial_validator()
    output_dir = tmp_path / "notebook_runs"
    output_dir.mkdir()
    for notebook_name in validator.EXPECTED_NOTEBOOKS:
        (output_dir / notebook_name).write_text(
            '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
            encoding="utf-8",
        )
    for artifact_name in validator.EXPECTED_ARTIFACTS:
        path = tmp_path / artifact_name
        if path.suffix == ".json":
            path.write_text("{}", encoding="utf-8")
        elif path.suffix == ".csv":
            path.write_text(
                ",".join(validator.CANONICAL_COLUMNS) + "\n"
                "fixture-team,site-1,1001,40,20,10,50\n",
                encoding="utf-8",
            )
        else:
            path.write_bytes(b"x")
    (tmp_path / "stale-output.json").write_text("{}", encoding="utf-8")

    assert validator.validate(output_dir) == 1


def _assert_relative_artifact_path(path, expected_root):
    assert isinstance(path, str) and path
    assert not path.endswith(("/", "\\"))
    assert "\\" not in path
    pure_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    assert not pure_path.is_absolute()
    assert not windows_path.is_absolute()
    assert not windows_path.root
    assert not windows_path.drive
    assert ".." not in pure_path.parts
    assert path.startswith(expected_root)
    assert pure_path != PurePosixPath(expected_root.rstrip("/"))
    assert pure_path.name


def _assert_schema_version(value):
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    assert value == 1


def test_tutorial_registry_lists_all_required_prompt_ids():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert [item["id"] for item in registry["prompts"]] == REQUIRED_PROMPT_IDS


def test_registry_has_expected_top_level_shape():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert isinstance(registry, dict)
    _assert_schema_version(registry["schema_version"])
    assert isinstance(registry["prompts"], list)
    assert registry["prompts"]


def test_registry_entries_have_expected_types_and_unique_ids():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entries = registry["prompts"]

    assert all(isinstance(item, dict) for item in entries)
    ids = [item["id"] for item in entries]
    assert len(ids) == len(set(ids))

    for item in entries:
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["module"], str) and item["module"]
        assert isinstance(item["prompt_file"], str) and item["prompt_file"]
        assert isinstance(item["transcript_file"], str) and item["transcript_file"]
        assert isinstance(item["notebook_files"], list)
        assert isinstance(item["required_sources"], list)
        assert item["required_sources"]
        assert isinstance(item["verification_requirements"], list)
        assert item["verification_requirements"]
        assert item["privacy_level"] == EXPECTED_PRIVACY_LEVEL
        assert all(isinstance(source, str) and source for source in item["required_sources"])
        assert all(
            isinstance(requirement, str) and requirement
            for requirement in item["verification_requirements"]
        )


def test_registry_artifact_references_are_rooted_relative_and_unique():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    prompt_paths = [item["prompt_file"] for item in registry["prompts"]]
    transcript_paths = [item["transcript_file"] for item in registry["prompts"]]
    notebook_paths = [
        path
        for item in registry["prompts"]
        for path in item["notebook_files"]
    ]

    for path in prompt_paths:
        _assert_relative_artifact_path(path, EXPECTED_PROMPT_ROOT)
    for path in transcript_paths:
        _assert_relative_artifact_path(path, EXPECTED_TRANSCRIPT_ROOT)
    for path in notebook_paths:
        _assert_relative_artifact_path(path, EXPECTED_NOTEBOOK_ROOT)

    assert len(prompt_paths) == len(set(prompt_paths))
    assert len(transcript_paths) == len(set(transcript_paths))
    assert len(notebook_paths) == len(set(notebook_paths))


@pytest.mark.parametrize(
    "path",
    [
        "prompts\\..\\transcripts\\01.json",
        "C:/tmp/prompt.md",
        "\\\\server\\share\\prompt.md",
        "prompts\\foo.md\\",
        "prompts/",
        "prompts/foo.md/",
    ],
)
def test_registry_path_helper_rejects_windows_or_directory_paths(path):
    with pytest.raises(AssertionError):
        _assert_relative_artifact_path(path, EXPECTED_PROMPT_ROOT)


def test_registry_path_helper_rejects_posix_absolute_path():
    with pytest.raises(AssertionError):
        _assert_relative_artifact_path("/tmp/prompt.md", EXPECTED_PROMPT_ROOT)


def test_registry_path_helper_rejects_posix_traversal_path():
    with pytest.raises(AssertionError):
        _assert_relative_artifact_path(
            "prompts/../transcripts/01.json", EXPECTED_PROMPT_ROOT
        )


def test_registry_schema_version_rejects_bool():
    with pytest.raises(AssertionError):
        _assert_schema_version(True)


def test_all_registered_prompts_have_required_headings():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    for item in registry["prompts"]:
        prompt = (REGISTRY.parent / item["prompt_file"]).read_text(encoding="utf-8")
        assert all(heading in prompt for heading in REQUIRED_PROMPT_HEADINGS)
        assert "distinguish verified facts, interpretations, and unknowns" in prompt.lower()


def test_all_registered_transcripts_use_curation_safe_schema():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    for item in registry["prompts"]:
        transcript = json.loads(
            (REGISTRY.parent / item["transcript_file"]).read_text(encoding="utf-8")
        )
        assert set(transcript) == TRANSCRIPT_KEYS
        assert transcript["prompt_id"] == item["id"]
        assert transcript["prompt"]
        assert transcript["response"]
        assert transcript["verification_checklist"]
        assert transcript["review_status"] == "fact-checked"


def test_latex_presentation_layer_has_required_source_contracts():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    bibliography = (TUTORIAL_ROOT / "references.bib").read_text(encoding="utf-8")

    for package in (
        "geometry",
        "newtxtext,newtxmath",
        "tcolorbox",
        "listings",
        "tabularx",
        "booktabs",
        "hyperref",
        "cleveref",
    ):
        assert package in main
    for environment in ("promptbox", "aibox", "verifybox", "checkpointbox", "warningbox", "outputbox"):
        assert f"{environment}" in main
    for phrase in ("AI-use", "privacy", "fact-check", "one-hour half-year", "student preview"):
        assert phrase.lower() in main.lower()
    assert "@misc{activsg2000" in bibliography


def test_latex_input_builder_escapes_plain_text_and_preserves_code(tmp_path):
    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    escaped = module.latex_escape(r"percent % ampersand & underscore _ hash # braces { } slash ")
    assert all(token in escaped for token in (r"\%", r"\&", r"\_", r"\#", r"\{", r"\}", r"\textbackslash{}"))
    result = __import__("subprocess").run(
        ["python3", str(builder), "--output-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "transcripts.tex").read_text(encoding="utf-8")
    assert "\\begin{lstlisting}" in rendered
    assert "assignment-overview" in rendered
        assert transcript["private_information_present"] is False


def test_transcript_validator_rejects_unsafe_missing_and_unregistered_sources(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root = tmp_path / "docs" / "student_tutorial"
    transcript_root = tutorial_root / "transcripts"
    transcript_root.mkdir(parents=True)
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    transcript = {
        "prompt_id": "example",
        "prompt": "prompt",
        "response": "response",
        "verification_checklist": ["check"],
        "agent_label": "agent",
        "generated_at": "2026-10-11",
        "review_status": "fact-checked",
        "review_notes": "review",
        "sources": ["README.md", "../outside.md"],
        "private_information_present": False,
    }
    (transcript_root / "example.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    registry = {
        "prompts": [
            {
                "id": "example",
                "transcript_file": "transcripts/example.json",
                "required_sources": ["README.md", "docs/missing.md"],
            }
        ]
    }
    registry_path = tutorial_root / "prompt_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def test_transcript_validator_accepts_existing_required_repository_sources(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root = tmp_path / "docs" / "student_tutorial"
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "docs" / "manual.md").write_text("manual\n", encoding="utf-8")
    (tutorial_root / "transcripts").mkdir(parents=True)
    transcript = {
        "prompt_id": "example",
        "prompt": "prompt",
        "response": "response",
        "verification_checklist": ["check"],
        "agent_label": "agent",
        "generated_at": "2026-10-11",
        "review_status": "fact-checked",
        "review_notes": "review",
        "sources": ["README.md", "docs/manual.md"],
        "private_information_present": False,
    }
    (tutorial_root / "transcripts" / "example.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "id": "example",
                "module": "Example",
                "prompt_file": "prompts/example.md",
                "transcript_file": "transcripts/example.json",
                "notebook_files": [],
                "required_sources": ["README.md", "docs/manual.md"],
                "verification_requirements": ["evidence"],
                "privacy_level": EXPECTED_PRIVACY_LEVEL,
            }
        ]
    }
    (tutorial_root / "prompts").mkdir()
    (tutorial_root / "prompts" / "example.md").write_text("prompt\n", encoding="utf-8")
    registry_path = tutorial_root / "prompt_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 0


def _write_minimal_validator_fixture(tmp_path, transcript, registry=None):
    validator_root = tmp_path / "docs" / "student_tutorial"
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (validator_root / "transcripts").mkdir(parents=True)
    (validator_root / "transcripts" / "example.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    if registry is None:
        registry = {
            "schema_version": 1,
            "prompts": [
                {
                    "id": "example",
                    "module": "Example",
                    "prompt_file": "prompts/example.md",
                    "transcript_file": "transcripts/example.json",
                    "notebook_files": [],
                    "required_sources": ["README.md"],
                    "verification_requirements": ["evidence"],
                    "privacy_level": EXPECTED_PRIVACY_LEVEL,
                }
            ],
        }
    (validator_root / "prompts").mkdir()
    (validator_root / "prompts" / "example.md").write_text("prompt\n", encoding="utf-8")
    registry_path = validator_root / "prompt_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return validator_root, registry_path


def _valid_minimal_transcript():
    return {
        "prompt_id": "example",
        "prompt": "prompt",
        "response": "A complete response grounded in the source.",
        "verification_checklist": ["check"],
        "agent_label": "agent",
        "generated_at": "2026-10-11",
        "review_status": "fact-checked",
        "review_notes": "review",
        "sources": ["README.md"],
        "private_information_present": False,
    }


def test_transcript_validator_reports_malformed_valid_json_instead_of_raising(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root, registry_path = _write_minimal_validator_fixture(
        tmp_path, ["not", "an", "object"]
    )
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def test_transcript_validator_reports_malformed_registry_shape_instead_of_raising(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root = tmp_path / "docs" / "student_tutorial"
    tutorial_root.mkdir(parents=True)
    registry_path = tutorial_root / "prompt_registry.json"
    registry_path.write_text(json.dumps({"schema_version": 1, "prompts": {}}), encoding="utf-8")
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def test_transcript_validator_rejects_source_symlink_escape(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    tutorial_root, registry_path = _write_minimal_validator_fixture(
        tmp_path, _valid_minimal_transcript()
    )
    (tmp_path / "README.md").unlink()
    (tmp_path / "README.md").symlink_to(outside)
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def test_transcript_validator_rejects_sibling_prefix_transcript_root(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root, registry_path = _write_minimal_validator_fixture(
        tmp_path,
        _valid_minimal_transcript(),
        registry={
            "schema_version": 1,
            "prompts": [
                {
                    "id": "example",
                    "module": "Example",
                    "prompt_file": "prompts/example.md",
                    "transcript_file": "transcripts_backup/example.json",
                    "notebook_files": [],
                    "required_sources": ["README.md"],
                    "verification_requirements": ["evidence"],
                    "privacy_level": EXPECTED_PRIVACY_LEVEL,
                }
            ],
        },
    )
    (tutorial_root / "transcripts_backup").mkdir()
    (tutorial_root / "transcripts_backup" / "example.json").write_text(
        json.dumps(_valid_minimal_transcript()), encoding="utf-8"
    )
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def _registry_validator_fixture(tmp_path, monkeypatch):
    validator = _load_registry_validator()
    monkeypatch.setattr(validator, "REQUIRED_IDS", ["example"])
    tutorial_root = tmp_path / "docs" / "student_tutorial"
    (tutorial_root / "prompts").mkdir(parents=True)
    (tutorial_root / "transcripts").mkdir()
    (tutorial_root / "notebooks").mkdir()
    (tutorial_root / "prompts" / "example.md").write_text(
        "\n".join(REQUIRED_PROMPT_HEADINGS), encoding="utf-8"
    )
    (tutorial_root / "transcripts" / "example.json").write_text("{}", encoding="utf-8")
    (tutorial_root / "notebooks" / "example.ipynb").write_text("{}", encoding="utf-8")
    registry = {
        "schema_version": 1,
        "prompts": [
            {
                "id": "example",
                "prompt_file": "prompts/example.md",
                "transcript_file": "transcripts/example.json",
                "notebook_files": ["notebooks/example.ipynb"],
            }
        ],
    }
    registry_path = tutorial_root / "prompt_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)
    return validator, registry_path, registry


def test_registry_validator_rejects_sibling_prefix_artifact_root(tmp_path, monkeypatch):
    validator, registry_path, registry = _registry_validator_fixture(tmp_path, monkeypatch)
    registry["prompts"][0]["prompt_file"] = "prompts_backup/example.md"
    (registry_path.parent / "prompts_backup").mkdir()
    (registry_path.parent / "prompts_backup" / "example.md").write_text(
        "\n".join(REQUIRED_PROMPT_HEADINGS), encoding="utf-8"
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert validator.validate_registry(registry_path) == 1


def test_registry_validator_rejects_symlink_escape(tmp_path, monkeypatch):
    validator, registry_path, registry = _registry_validator_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("\n".join(REQUIRED_PROMPT_HEADINGS), encoding="utf-8")
    prompt = registry_path.parent / "prompts" / "example.md"
    prompt.unlink()
    prompt.symlink_to(outside)

    assert validator.validate_registry(registry_path) == 1


def test_registry_validator_rejects_missing_notebook(tmp_path, monkeypatch):
    validator, registry_path, registry = _registry_validator_fixture(tmp_path, monkeypatch)
    registry_path.parent.joinpath("notebooks", "example.ipynb").unlink()

    assert validator.validate_registry(registry_path) == 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("response", ["not text"]),
        ("verification_checklist", ["ok", 3]),
        ("sources", ["README.md", 3]),
        ("private_information_present", "false"),
    ],
)
def test_transcript_validator_rejects_wrong_transcript_field_types(
    tmp_path, monkeypatch, field, value
):
    validator = _load_transcript_validator()
    transcript = _valid_minimal_transcript()
    transcript[field] = value
    tutorial_root, registry_path = _write_minimal_validator_fixture(tmp_path, transcript)
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


def test_transcript_validator_rejects_placeholder_response(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    transcript = _valid_minimal_transcript()
    transcript["response"] = "CURATION PENDING"
    tutorial_root, registry_path = _write_minimal_validator_fixture(tmp_path, transcript)
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1
