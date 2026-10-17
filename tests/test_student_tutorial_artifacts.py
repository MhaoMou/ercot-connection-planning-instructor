import json
import hashlib
import importlib.util
import re
import subprocess
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_VALIDATOR = REPO_ROOT / "docs" / "student_tutorial" / "scripts" / "validate_tutorial_examples.py"
TUTORIAL_ROOT = REPO_ROOT / "docs" / "student_tutorial"

EXPECTED_PRIVACY_LEVEL = "public-or-instructor-approved-fixture"
EXPECTED_NOTEBOOK_ROOT = "notebooks/"
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
    assert "every 6th six-hour period (every 36 hours)" in main
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
































def _load_pdf_builder(name):
    script = TUTORIAL_ROOT / "scripts" / "build_pdf.py"
    module_spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    return module


def test_tutorial_latex_source_is_deterministic():
    main = (TUTORIAL_ROOT / "main.tex").read_text(encoding="utf-8")
    assert r"\today" not in main
    assert r"\input{generated/" not in main


def test_tutorial_pdf_build_uses_only_local_toolchain(monkeypatch, tmp_path):
    module = _load_pdf_builder("build_pdf_offline")
    (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
    monkeypatch.setattr(module, "run_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)

    module.build(tutorial_root=tmp_path)

    assert module.LATEXMK in {"latexmk", "tectonic", "pdflatex"}


def test_tutorial_pdf_build_requires_bibliography_toolchain(monkeypatch, tmp_path):
    module = _load_pdf_builder("build_pdf_bibtool")
    (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/latexmk" if name == module.LATEXMK else None)

    with pytest.raises(RuntimeError, match="bibliography toolchain.*bibtex"):
        module.build(tutorial_root=tmp_path)


def test_tutorial_pdf_clean_builds_are_byte_and_metadata_reproducible(tmp_path):
    module = _load_pdf_builder("build_pdf_reproducible")
    pdfs = [
        module.build(tutorial_root=TUTORIAL_ROOT, build_dir=tmp_path / f"build-{index}")
        for index in (1, 2)
    ]
    assert all(pdf.is_file() for pdf in pdfs)
    assert hashlib.sha256(pdfs[0].read_bytes()).hexdigest() == hashlib.sha256(
        pdfs[1].read_bytes()
    ).hexdigest()
    metadata = []
    for pdf in pdfs:
        info = subprocess.run(["pdfinfo", str(pdf)], check=True, text=True, capture_output=True).stdout
        metadata.append(
            "\n".join(line for line in info.splitlines() if not line.startswith(("File size:", "Pages:")))
        )
    assert metadata[0] == metadata[1]
