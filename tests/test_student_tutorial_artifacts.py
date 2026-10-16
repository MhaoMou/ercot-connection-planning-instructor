import json
import hashlib
import importlib.util
import re
import subprocess
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
RENDERED_FIGURES = [
    "pipeline.tex",
    "student_vs_evaluator.tex",
    "storage_soc.tex",
]
SOURCE_ONLY_FIGURES = [
    "csv_evaluation_flow.tex",
    "data_relationships.tex",
    "strategy_experiment.tex",
    "three_bus_dcopf.tex",
]
ALL_FIGURES = RENDERED_FIGURES + SOURCE_ONLY_FIGURES
EARLY_PROMPT_SECTIONS = (
    "Explore the data",
    "Write and check your CSV",
    "Develop a strategy",
)
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


def test_student_tutorial_has_all_planned_tikz_figures():
    figures = TUTORIAL_ROOT / "figures"
    assert [path.name for path in sorted(figures.glob("*.tex"))] == sorted(ALL_FIGURES)


def test_rendered_figures_are_integrated_with_captions():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    figure_blocks = re.findall(r"\\begin\{figure\}.*?\\end\{figure\}", main, re.DOTALL)
    for figure_name in RENDERED_FIGURES:
        stem = Path(figure_name).stem
        block = next((block for block in figure_blocks if f"figures/{stem}" in block), "")
        assert block, f"rendered figure {figure_name} is not integrated in main.tex"
        assert "\\caption{" in block, f"rendered figure {figure_name} has no caption"


def test_source_only_figures_remain_in_inventory_without_rendered_inclusion():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    figures = TUTORIAL_ROOT / "figures"
    for figure_name in SOURCE_ONLY_FIGURES:
        assert (figures / figure_name).is_file()
        assert f"figures/{Path(figure_name).stem}" not in "\n".join(
            line for line in main.splitlines() if not line.lstrip().startswith("%")
        )


def test_early_sections_contain_contextual_prompt_blocks():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    for section_name in EARLY_PROMPT_SECTIONS:
        start = main.index(f"\\section{{{section_name}}}")
        next_section_match = re.search(r"^\\section\{", main[start + 1 :], re.MULTILINE)
        next_section = start + 1 + next_section_match.start() if next_section_match else -1
        section = main[start:] if next_section == -1 else main[start:next_section]
        assert "\\begin{promptbox}" in section


def test_tikz_figures_use_current_architecture_terms_only():
    forbidden = ("teaching bus", "path corridor", "planning region", "six-period")
    required_by_figure = {
        "pipeline.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
        "data_relationships.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
        "student_vs_evaluator.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
        "three_bus_dcopf.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
        "csv_evaluation_flow.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
        "strategy_experiment.tex": ("ACTIVSg2000", "student CSV", "DCOPF", "student preview"),
    }
    for figure_name in ALL_FIGURES:
        text = (TUTORIAL_ROOT / "figures" / figure_name).read_text(encoding="utf-8")
        lowered = text.lower()
        assert not any(term in lowered for term in forbidden)
        required = required_by_figure.get(figure_name, ())
        assert all(term.lower() in lowered for term in required)


def test_task5_pipeline_assigns_context_to_package_and_csv_to_student():
    pipeline = (TUTORIAL_ROOT / "figures" / "pipeline.tex").read_text(encoding="utf-8").lower()
    caption = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8").lower()

    assert "hourly source data" in pipeline
    assert "six-hour release" in pipeline
    assert "6-hour profiles" in pipeline
    assert "student creates" in pipeline
    assert "package -- (csv)" not in pipeline
    assert "feeds the seven-column site csv" not in caption
    assert "context and validation inputs" in caption


def test_task5_csv_flow_separates_exact_evaluator_from_preview():
    flow = (TUTORIAL_ROOT / "figures" / "csv_evaluation_flow.tex").read_text(encoding="utf-8").lower()
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8").lower()

    assert "six-hour sampled" in flow
    assert "idle-storage" in flow
    assert "non-authoritative screening" in flow
    assert "exact instructor evaluator" in flow
    assert "joint storage-aware" in flow
    assert "dcopf/lp" in flow
    assert "every 6th hour" in main
    assert "not dispatched (idle)" in main
    assert "dispatched independently within each 24-hour block" in main


def test_task5_csv_flow_gives_output_boxes_a_clear_vertical_gap():
    flow = (TUTORIAL_ROOT / "figures" / "csv_evaluation_flow.tex").read_text(encoding="utf-8")

    assert "right=of checked, yshift=12mm" in flow
    assert "right=of checked, yshift=-12mm" in flow


def test_task5_strategy_figure_uses_synthetic_not_activsg2000_inputs():
    strategy = (TUTORIAL_ROOT / "figures" / "strategy_experiment.tex").read_text(encoding="utf-8").lower()

    assert "notebook 07" in strategy
    assert "synthetic candidate plans" in strategy
    assert "hand-built preview metrics" in strategy
    assert "activsg2000-derived inputs" not in strategy


def test_task5_strategy_figure_has_bounded_layout():
    strategy = (TUTORIAL_ROOT / "figures" / "strategy_experiment.tex").read_text(encoding="utf-8")

    assert "text width=25mm" in strategy
    assert "minimum height=10mm" in strategy
    assert "right=of fixture" in strategy
    assert "right=of variant" in strategy
    assert "right=of validate" in strategy
    assert "right=of preview" in strategy


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
        assert transcript["private_information_present"] is False


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
        "xurl",
        "cleveref",
    ):
        assert package in main
    for environment in ("keybox", "warningbox", "exercisebox", "promptbox", "aibox"):
        assert f"{environment}" in main
    assert "breaklines=true" in main
    assert "breakatwhitespace=false" in main
    assert "emergencystretch=3em" in main
    for phrase in ("Using AI", "privacy", "six-hour half-year", "student_preview"):
        assert phrase.lower() in main.lower()
    assert "@misc{activsg2000" in bibliography


def test_latex_input_builder_escapes_plain_text_and_preserves_code(tmp_path):
    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    escaped = module.latex_escape(r"percent % ampersand & underscore _ hash # braces { } dollar $ slash \\ marker")
    assert all(token in escaped for token in (r"\%", r"\&", r"\_", r"\#", r"\{", r"\}", r"\$", r"\textbackslash{}"))
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
    assert r"\path{" in module._markdown_text("a long `team_id,site_id,bus_id` value")
    assert "assignment-overview" in rendered


def test_latex_input_builder_runs_both_offline_validators(monkeypatch, tmp_path):
    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs_validators", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module, "validate_sources", lambda: calls.append("validated"))

    module.build(tmp_path)

    assert calls == ["validated"]


def test_latex_input_builder_rejects_unreviewed_or_placeholder_transcripts(monkeypatch, tmp_path):
    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs_policy", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    monkeypatch.setattr(module, "validate_sources", lambda: None)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "prompt_registry.json").write_text(
        json.dumps({"prompts": [{"id": "example", "module": "Example", "transcript_file": "transcripts/example.json"}]}),
        encoding="utf-8",
    )
    transcript = {
        "prompt": "prompt",
        "response": "[placeholder]",
        "verification_checklist": ["check"],
        "review_status": "pending",
    }
    (tmp_path / "transcripts/example.json").write_text(json.dumps(transcript), encoding="utf-8")

    with pytest.raises(ValueError, match="fact-checked|placeholder"):
        module.build(tmp_path / "generated")


def test_latex_input_builder_escapes_literal_listing_end_marker():
    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs_listing", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    rendered = module.render_response("```python\nprint(r'\\end{lstlisting}')\n```")

    assert r"\textbackslash end\{lstlisting\}" in rendered
    assert rendered.count(r"\begin{lstlisting}") == rendered.count(r"\end{lstlisting}")


def test_tutorial_latex_source_and_builder_are_offline_and_deterministic():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    builder = (TUTORIAL_ROOT / "scripts" / "build_latex_inputs.py").read_text(encoding="utf-8")

    assert r"\today" not in main
    assert "fact-checked" in builder
    assert "openai" not in builder.lower()
    assert "requests" not in builder.lower()


def test_latex_input_builder_succeeds_without_network_or_model_access(monkeypatch, tmp_path):
    import socket

    builder = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "build_latex_inputs.py"
    module_spec = importlib.util.spec_from_file_location("build_latex_inputs_offline", builder)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    def fail_network(*args, **kwargs):
        raise AssertionError("network access is forbidden during tutorial input generation")

    monkeypatch.setattr(socket, "socket", fail_network)
    assert module.build(tmp_path) == 13


def test_tutorial_pdf_build_fails_when_generated_inputs_are_missing(monkeypatch, tmp_path):
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_missing_inputs", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    monkeypatch.setattr(module, "run_input_builder", lambda *args: None)

    with pytest.raises(RuntimeError, match="generated/transcripts.tex"):
        module.build(tutorial_root=tmp_path)


def test_tutorial_pdf_build_uses_only_local_toolchain(monkeypatch, tmp_path):
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_offline", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "transcripts.tex").write_text("% generated\n", encoding="utf-8")
    (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
    monkeypatch.setattr(module, "run_input_builder", lambda *args: None)
    monkeypatch.setattr(module, "run_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)

    module.build(tutorial_root=tmp_path)

    assert module.LATEXMK in {"latexmk", "tectonic", "pdflatex"}


def test_tutorial_pdf_build_commands_disable_shell_escape():
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_commands", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    latexmk_command = module._latexmk_command(Path("main.tex"), Path("build"))
    pdflatex_command = module._pdflatex_command(Path("main.tex"), Path("build"))

    assert "-no-shell-escape" in latexmk_command
    assert "-no-shell-escape" in pdflatex_command


def test_tutorial_pdf_clean_builds_are_byte_and_metadata_reproducible(tmp_path):
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_reproducible", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    source_generated = TUTORIAL_ROOT / "generated" / "transcripts.tex"
    source_generated_before = (
        source_generated.read_bytes() if source_generated.is_file() else None
    )
    pdfs = []
    for index in (1, 2):
        generated_dir = tmp_path / f"generated-{index}"
        pdfs.append(
            module.build(
                tutorial_root=TUTORIAL_ROOT,
                build_dir=tmp_path / f"build-{index}",
                generated_dir=generated_dir,
            )
        )
        assert (generated_dir / "transcripts.tex").is_file()

    source_generated_after = (
        source_generated.read_bytes() if source_generated.is_file() else None
    )
    assert source_generated_after == source_generated_before

    assert all(pdf.is_file() for pdf in pdfs)
    assert hashlib.sha256(pdfs[0].read_bytes()).hexdigest() == hashlib.sha256(
        pdfs[1].read_bytes()
    ).hexdigest()
    metadata = []
    for pdf in pdfs:
        info = subprocess.run(
            ["pdfinfo", str(pdf)],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        metadata.append(
            "\n".join(
                line
                for line in info.splitlines()
                if not line.startswith(("File size:", "Pages:"))
            )
        )
    assert metadata[0] == metadata[1]


def test_tutorial_pdf_build_requires_bibliography_toolchain(monkeypatch, tmp_path):
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_bibtool", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "transcripts.tex").write_text("% generated\n", encoding="utf-8")
    (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
    monkeypatch.setattr(module, "run_input_builder", lambda *args: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/latexmk" if name == module.LATEXMK else None)

    with pytest.raises(RuntimeError, match="bibliography toolchain.*bibtex"):
        module.build(tutorial_root=tmp_path)


def test_tutorial_pdf_build_rejects_bibliography_warnings():
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_bibwarning", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    with pytest.raises(RuntimeError, match="bibliography warnings"):
        module._check_build_output("Warning--I didn't find a database entry for 'missing'")


def test_tutorial_pdf_build_allows_documented_underfull_warnings():
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_underfull", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    module._check_build_output("Underfull \\hbox (badness 10000) in paragraph")


def test_tutorial_pdf_build_rejects_overfull_boxes():
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location("build_pdf_overfull", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    with pytest.raises(RuntimeError, match="overfull boxes"):
        module._check_build_output("Overfull \\hbox (12.0pt too wide) in paragraph")


def test_tutorial_build_docs_and_hygiene_rules_are_present():
    readme = (TUTORIAL_ROOT / "README.md").read_text(encoding="utf-8")
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "python3 docs/student_tutorial/scripts/build_pdf.py" in readme
    assert "-no-shell-escape" in readme
    assert "bibliography warnings" in readme
    assert "Underfull" in readme
    assert "overfull boxes" in readme
    assert "docs/student_tutorial/generated/" in ignore
    assert "docs/student_tutorial/build/" in ignore
    assert "docs/student_tutorial/*.pdf" in ignore


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


def test_transcript_validator_rejects_source_not_listed_in_registry(tmp_path, monkeypatch):
    validator = _load_transcript_validator()
    tutorial_root = tmp_path / "docs" / "student_tutorial"
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
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
    (tutorial_root / "transcripts" / "example.json").write_text(json.dumps(transcript), encoding="utf-8")
    (tutorial_root / "prompts").mkdir()
    (tutorial_root / "prompts" / "example.md").write_text("prompt\n", encoding="utf-8")
    registry = {
        "schema_version": 1,
        "prompts": [{
            "id": "example", "module": "Example", "prompt_file": "prompts/example.md",
            "transcript_file": "transcripts/example.json", "notebook_files": [],
            "required_sources": ["README.md"], "verification_requirements": ["evidence"],
            "privacy_level": EXPECTED_PRIVACY_LEVEL,
        }],
    }
    registry_path = tutorial_root / "prompt_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(validator, "TUTORIAL_ROOT", tutorial_root)

    assert validator.validate_transcripts(registry_path) == 1


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
