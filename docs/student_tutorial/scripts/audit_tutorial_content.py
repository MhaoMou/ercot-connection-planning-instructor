#!/usr/bin/env python3
"""Audit the student-facing tutorial contract without calling a model."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_IDS = [
    "assignment-overview", "dataset-inspection", "python-visualization",
    "matlab-visualization", "dcopf-concept", "dcopf-formulation",
    "three-bus-implementation", "csv-format", "storage-model", "colab-preview",
    "debugging", "strategy-experiments", "reproducibility",
]
_PROMPT_NAMES = [
    ("assignment-overview", "assignment_overview"), ("dataset-inspection", "dataset_inspection"),
    ("python-visualization", "python_visualization"), ("matlab-visualization", "matlab_visualization"),
    ("dcopf-concept", "dcopf_concept"), ("dcopf-formulation", "dcopf_formulation"),
    ("three-bus-implementation", "three_bus_implementation"), ("csv-format", "csv_format"),
    ("storage-model", "storage_model"), ("colab-preview", "colab_preview"),
    ("debugging", "debugging"), ("strategy-experiments", "strategy_experiments"),
    ("reproducibility", "reproducibility"),
]
EXPECTED_PROMPT_FILES = {
    prompt_id: f"prompts/{index:02d}_{name}.md"
    for index, (prompt_id, name) in enumerate(_PROMPT_NAMES, start=1)
}
EXPECTED_TRANSCRIPT_FILES = {
    prompt_id: path.replace("prompts/", "transcripts/").replace(".md", ".json")
    for prompt_id, path in EXPECTED_PROMPT_FILES.items()
}
EXPECTED_NOTEBOOKS = {
    "01_dataset_inspection.ipynb", "02_network_visualization.ipynb",
    "03_three_bus_dcopf.ipynb", "04_storage_soc.ipynb", "05_csv_validation.ipynb",
    "06_student_preview.ipynb", "07_strategy_ablation.ipynb",
}
EXPECTED_NOTEBOOK_ASSOCIATIONS = {
    "dataset-inspection": "notebooks/01_dataset_inspection.ipynb",
    "python-visualization": "notebooks/02_network_visualization.ipynb",
    "three-bus-implementation": "notebooks/03_three_bus_dcopf.ipynb",
    "storage-model": "notebooks/04_storage_soc.ipynb",
    "csv-format": "notebooks/05_csv_validation.ipynb",
    "colab-preview": "notebooks/06_student_preview.ipynb",
    "strategy-experiments": "notebooks/07_strategy_ablation.ipynb",
}
RENDERED_FIGURES = (
    "pipeline.tex",
    "student_vs_evaluator.tex",
    "storage_soc.tex",
)
SOURCE_ONLY_FIGURES = (
    "csv_evaluation_flow.tex",
    "data_relationships.tex",
    "strategy_experiment.tex",
    "three_bus_dcopf.tex",
)
EXPECTED_FIGURES = set(RENDERED_FIGURES) | set(SOURCE_ONLY_FIGURES)
EARLY_CONTEXTUAL_PROMPT_SECTIONS = (
    "Explore the data",
    "Write and check your CSV",
    "Develop a strategy",
)
REQUIRED_PROMPT_HEADINGS = (
    "# Prompt metadata", "## Goal", "## Context supplied to the AI",
    "## Prompt", "## Expected evidence", "## Privacy constraints",
)
LEGACY_PATTERNS = {
    "six-period terminology": re.compile(r"\bsix[- ]period(?:s)?\b", re.I),
    "intervention field": re.compile(r"\bintervention\b", re.I),
    "curtailment field": re.compile(r"\bcurtailment\b", re.I),
    "path-corridor field": re.compile(r"\bpath[- ]corridor\b", re.I),
    "teaching-bus terminology": re.compile(r"\bteaching bus\b", re.I),
    "obsolete hourly submission field": re.compile(
        r"(?<![A-Za-z0-9_-])(?:period|load_mw|submission_id|generator_id|storage_id)(?![A-Za-z0-9_-])", re.I
    ),
}
EXPLICIT_REJECTION = re.compile(
    r"\b(?:reject(?:ed|s)?|not\s+(?:a\s+)?current|not\s+(?:part\s+of|contain|include|use)|"
    r"do\s+not\s+add|not\s+hourly|no\s+hourly)\b", re.I
)
PRIVATE_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\|/private/var/)", re.I)
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_ -]?key|access[_ -]?token|password)\s*[:=])", re.I
)
PRIVACY_PATTERNS = (
    re.compile(r"\b(?:team|group)\s+name\s*[:=]\s*[A-Za-z][\w-]{2,}", re.I),
    re.compile(r"\b(?:plan|submission)[-_ ]?id\s*[:=]\s*[A-Z0-9][A-Z0-9_-]{5,}", re.I),
    re.compile(r"\b(?:rank|ranking|leaderboard)\s*[:=#]\s*\d+\b", re.I),
    re.compile(r"\binstructor[- ]only\s+(?:diagnostic|log|output|result)\s*[:=]\s*\S+", re.I),
)


def _repository_root(tutorial_root: Path) -> Path:
    return tutorial_root.parent.parent if tutorial_root.parent.name == "docs" else tutorial_root


def _safe_path(base: Path, value: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: path must be a non-null string")
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        errors.append(f"{label}: path must be repository-relative")
        return None
    base_resolved = base.resolve()
    resolved = (base / candidate).resolve(strict=False)
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        errors.append(f"{label}: path resolves outside tutorial root or escapes via symlink")
        return None
    return resolved


def _read_text(path: Path, label: str, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label}: cannot read text: {exc}")
        return None


def _read_json_object(path: Path, label: str, errors: list[str]) -> dict | None:
    raw = _read_text(path, label, errors)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label}: JSON value must be an object")
        return None
    return value


def _strip_latex_comments(text: str) -> str:
    """Remove unescaped LaTeX comments while preserving line boundaries."""
    active_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        backslashes = 0
        comment_index = None
        for index, character in enumerate(line):
            if character == "%" and backslashes % 2 == 0:
                comment_index = index
                break
            if character == "\\":
                backslashes += 1
            else:
                backslashes = 0
        if comment_index is None:
            active_lines.append(line)
        else:
            newline = line[len(line.rstrip("\r\n")) :]
            active_lines.append(line[:comment_index] + newline)
    return "".join(active_lines)


def _is_explicit_legacy_context(line: str, match: re.Match[str]) -> bool:
    """Return whether a legacy term is explicitly labeled, not merely nearby."""
    if EXPLICIT_REJECTION.search(line):
        return True
    term = re.escape(match.group(0))
    before = line[: match.start()]
    after = line[match.end() :]
    marked_before = re.search(
        rf"\b(?:historical|legacy|retired|obsolete|deprecated|comparison)\b"
        rf"(?:[\s`*_/-]+[A-Za-z0-9]+){{0,3}}[\s`*_/-]*{term}\b",
        before + match.group(0),
        re.I,
    )
    marked_after = re.search(
        rf"{term}\b(?:[\s`*_/-]+[A-Za-z0-9]+){{0,4}}[\s`*_/-]+"
        rf"(?:historical|legacy|retired|obsolete|deprecated|comparison)\b",
        match.group(0) + after,
        re.I,
    )
    return bool(marked_before or marked_after)


def _read_student_texts(root: Path, errors: list[str]) -> list[tuple[str, str]]:
    paths = [
        root / "README.md",
        root / "main.tex",
        root / "references.bib",
        root / "generated" / "transcripts.tex",
    ]
    paths += sorted((root / "prompts").glob("*.md"))
    paths += sorted((root / "transcripts").glob("*.json"))
    paths += sorted((root / "figures").glob("*.tex"))
    texts: list[tuple[str, str]] = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        safe = _safe_path(root, name, name, errors)
        if safe is None or not safe.is_file():
            continue
        if path.suffix == ".json" and path.parent.name == "transcripts":
            record = _read_json_object(safe, name, errors)
            if record is None:
                continue
            parts = [record.get(key, "") for key in ("prompt", "response", "review_notes")]
            parts += ["\n".join(value for value in record.get(key, []) if isinstance(value, str)) for key in ("verification_checklist", "sources") if isinstance(record.get(key), list)]
            texts.append((name, "\n".join(str(part) for part in parts if part is not None)))
        else:
            raw = _read_text(safe, name, errors)
            if raw is not None:
                texts.append((name, raw))
    for path in sorted((root / "notebooks").glob("*.ipynb")):
        name = path.relative_to(root).as_posix()
        safe = _safe_path(root, name, name, errors)
        if safe is None or not safe.is_file():
            continue
        notebook = _read_json_object(safe, name, errors)
        if notebook is None:
            continue
        cells = notebook.get("cells")
        if not isinstance(cells, list):
            errors.append(f"{name}: cells must be a non-null list")
            continue
        markdown = "\n".join(str(cell.get("source", "")) for cell in cells if isinstance(cell, dict) and cell.get("cell_type") == "markdown")
        texts.append((name + ":markdown", markdown))
    return texts


def _check_transcript(
    prompt_id: str,
    transcript: dict,
    errors: list[str],
    repository_root: Path,
    allowed_sources: object,
) -> None:
    for key in ("prompt", "response", "review_notes"):
        if not isinstance(transcript.get(key), str) or not transcript[key].strip():
            errors.append(f"{prompt_id}: transcript {key} must be a non-null string")
    checklist = transcript.get("verification_checklist")
    sources = transcript.get("sources")
    if not isinstance(checklist, list) or any(not isinstance(value, str) or not value.strip() for value in checklist):
        errors.append(f"{prompt_id}: verification checklist must be a non-null list of strings")
    if not isinstance(sources, list) or any(not isinstance(value, str) or not value.strip() for value in sources):
        errors.append(f"{prompt_id}: transcript sources must be a non-null list of strings")
    else:
        for source in sources:
            source_path = _safe_path(
                repository_root, source, f"{prompt_id}: transcript source {source}", errors
            )
            if source_path is not None and not source_path.is_file():
                errors.append(f"{prompt_id}: missing transcript source {source}")
            if isinstance(allowed_sources, list) and source not in allowed_sources:
                errors.append(
                    f"{prompt_id}: transcript source is not listed in registry required_sources: {source}"
                )
    if transcript.get("prompt_id") != prompt_id:
        errors.append(f"{prompt_id}: prompt/transcript ID mismatch")
    if transcript.get("review_status") != "fact-checked":
        errors.append(f"{prompt_id}: transcript is not fact-checked")
    if transcript.get("private_information_present") is not False:
        errors.append(f"{prompt_id}: transcript contains private information")


def _check_registry(root: Path, errors: list[str]) -> dict:
    registry_path = _safe_path(root, "prompt_registry.json", "registry", errors)
    if registry_path is None or not registry_path.is_file():
        if registry_path is not None:
            errors.append("registry: prompt_registry.json is missing")
        return {}
    registry = _read_json_object(registry_path, "registry", errors)
    if registry is None:
        return {}
    entries = registry.get("prompts")
    if not isinstance(entries, list):
        errors.append("registry: prompts must be a non-null list")
        return registry
    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if ids != REQUIRED_IDS:
        errors.append(f"registry IDs must be exactly {REQUIRED_IDS!r}")
    if len(ids) != len(set(ids)):
        errors.append("registry IDs must be unique")
    seen_paths: dict[str, set[str]] = {field: set() for field in ("prompt_file", "transcript_file", "notebook_files")}
    repo_root = _repository_root(root)
    for index, entry in enumerate(entries):
        label = f"registry entry {index}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        prompt_id = entry.get("id", "<missing>")
        if not isinstance(prompt_id, str) or not prompt_id:
            errors.append(f"{label}: id must be a non-null string")
            prompt_id = str(prompt_id)
        for key in ("prompt_file", "transcript_file"):
            path = _safe_path(root, entry.get(key), f"{prompt_id}: {key}", errors)
            if path is None:
                continue
            relative = path.relative_to(root.resolve()).as_posix()
            if relative in seen_paths[key]:
                errors.append(f"{prompt_id}: duplicate {key} path {relative}")
            seen_paths[key].add(relative)
            if not path.is_file():
                errors.append(f"{prompt_id}: missing {key}")
            elif key == "prompt_file":
                prompt = _read_text(path, f"{prompt_id}: prompt", errors)
                if prompt is not None:
                    for heading in REQUIRED_PROMPT_HEADINGS:
                        if heading not in prompt:
                            errors.append(f"{prompt_id}: prompt missing {heading}")
            else:
                transcript = _read_json_object(path, f"{prompt_id}: transcript", errors)
                if transcript is not None:
                    _check_transcript(
                        prompt_id,
                        transcript,
                        errors,
                        repo_root,
                        entry.get("required_sources"),
                    )
        notebook_files = entry.get("notebook_files")
        if not isinstance(notebook_files, list) or any(not isinstance(value, str) for value in notebook_files):
            errors.append(f"{prompt_id}: notebook_files must be a non-null list of strings")
            notebook_files = []
        for value in notebook_files:
            path = _safe_path(root, value, f"{prompt_id}: notebook_file", errors)
            if path is None:
                continue
            relative = path.relative_to(root.resolve()).as_posix()
            if relative in seen_paths["notebook_files"]:
                errors.append(f"{prompt_id}: duplicate notebook_files path {relative}")
            seen_paths["notebook_files"].add(relative)
            if not path.is_file():
                errors.append(f"{prompt_id}: missing notebook {relative}")
            else:
                _read_json_object(path, f"{prompt_id}: notebook {relative}", errors)
        required_sources = entry.get("required_sources")
        if not isinstance(required_sources, list) or any(not isinstance(value, str) for value in required_sources):
            errors.append(f"{prompt_id}: required_sources must be a non-null list of strings")
            required_sources = []
        for source in required_sources:
            source_path = _safe_path(repo_root, source, f"{prompt_id}: required source {source}", errors)
            if source_path is not None and not source_path.is_file():
                errors.append(f"{prompt_id}: missing required source {source}")
        if prompt_id in EXPECTED_PROMPT_FILES and entry.get("prompt_file") != EXPECTED_PROMPT_FILES[prompt_id]:
            errors.append(f"{prompt_id}: prompt_file association is incorrect")
        if prompt_id in EXPECTED_TRANSCRIPT_FILES and entry.get("transcript_file") != EXPECTED_TRANSCRIPT_FILES[prompt_id]:
            errors.append(f"{prompt_id}: transcript_file association is incorrect")
        expected_notebook = EXPECTED_NOTEBOOK_ASSOCIATIONS.get(prompt_id)
        if (expected_notebook is None and notebook_files) or (expected_notebook is not None and notebook_files != [expected_notebook]):
            errors.append(f"{prompt_id}: notebook association is incorrect")
    if seen_paths["prompt_file"] != set(EXPECTED_PROMPT_FILES.values()):
        errors.append("registry prompt_file set must exactly match the 13 expected prompt files")
    if seen_paths["transcript_file"] != set(EXPECTED_TRANSCRIPT_FILES.values()):
        errors.append("registry transcript_file set must exactly match the 13 expected transcript files")
    return registry


def _check_coverage(
    root: Path,
    registry: dict,
    texts: list[tuple[str, str]],
    errors: list[str],
    *,
    allow_unbuilt: bool = False,
) -> None:
    entries = registry.get("prompts", []) if isinstance(registry, dict) else []
    registered_notebooks = {Path(path).name for entry in entries if isinstance(entry, dict) for path in entry.get("notebook_files", []) if isinstance(path, str)}
    if registered_notebooks != EXPECTED_NOTEBOOKS:
        errors.append("registry must cover exactly the seven notebooks")
    actual_notebooks = {path.name for path in (root / "notebooks").glob("*.ipynb") if path.is_file()}
    if actual_notebooks != EXPECTED_NOTEBOOKS:
        errors.append("tutorial must contain exactly the seven required notebooks")
    actual_figures = {path.name for path in (root / "figures").glob("*.tex") if path.is_file()}
    if actual_figures != EXPECTED_FIGURES:
        errors.append("tutorial must contain exactly the seven required figures")
    main = next((text for name, text in texts if name == "main.tex"), "")
    active_main = _strip_latex_comments(main)
    figure_blocks = re.findall(r"\\begin\{figure\}.*?\\end\{figure\}", active_main, re.DOTALL)
    for figure in RENDERED_FIGURES:
        reference = f"figures/{Path(figure).stem}"
        if reference not in active_main:
            errors.append(f"main.tex does not integrate figure {figure}")
        if not any(reference in block and "\\caption{" in block for block in figure_blocks):
            errors.append(f"rendered figure {figure} must have an integrated caption")
    generated = _safe_path(
        root, "generated/transcripts.tex", "generated/transcripts.tex", errors
    )
    if generated is None or not generated.is_file():
        if not allow_unbuilt:
            errors.append("generated/transcripts.tex is required for the release audit")
    else:
        generated_text = _read_text(generated, "generated/transcripts.tex", errors) or ""
        for prompt_id in REQUIRED_IDS:
            if f"transcript:{prompt_id}" not in generated_text:
                errors.append(f"generated transcripts do not integrate module {prompt_id}")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prompt_id = entry.get("id", "<missing>")
        transcript_path = _safe_path(root, entry.get("transcript_file"), f"{prompt_id}: transcript_file", errors)
        transcript = _read_json_object(transcript_path, f"{prompt_id}: transcript", errors) if transcript_path and transcript_path.is_file() else None
        if transcript is None:
            continue
        sources = set(transcript.get("sources", [])) if isinstance(transcript.get("sources"), list) else set()
        required_sources = entry.get("required_sources", [])
        for source in required_sources if isinstance(required_sources, list) else []:
            if source not in sources:
                errors.append(f"{prompt_id}: source coverage missing required source {source}")


def _check_content_contract(root: Path, texts: list[tuple[str, str]], errors: list[str]) -> None:
    combined = "\n".join(text for _, text in texts)
    if "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh" not in combined:
        errors.append("tutorial does not show the exact seven-column canonical CSV header")
    if "one-hour half-year" not in combined.casefold():
        errors.append("tutorial must explicitly say one-hour half-year")
    if "4,368" not in combined or "4,416" not in combined:
        errors.append("tutorial must include current 4,368 and 4,416 hour semantics")
    if not re.search(r"4,368.{0,120}(?:jan(?:uary)?[-– ]*jun(?:e)?|2028)", combined, re.I | re.S) and not re.search(r"(?:jan(?:uary)?[-– ]*jun(?:e)?|2028).{0,120}4,368", combined, re.I | re.S):
        errors.append("tutorial must identify 4,368 as the 2028 January--June horizon")
    if not re.search(r"4,416.{0,120}(?:jul(?:y)?[-– ]*dec(?:ember)?|second half)", combined, re.I | re.S) and not re.search(r"(?:jul(?:y)?[-– ]*dec(?:ember)?|second half).{0,120}4,416", combined, re.I | re.S):
        errors.append("tutorial must identify 4,416 as the July--December horizon")
    lower = combined.casefold()
    if "ai" not in lower or "privacy" not in lower or "do not upload" not in lower:
        errors.append("tutorial must provide explicit AI-use and privacy guidance")
    main = next((text for name, text in texts if name == "main.tex"), "")
    for section_name in EARLY_CONTEXTUAL_PROMPT_SECTIONS:
        match = re.search(
            rf"^\\section\{{{re.escape(section_name)}\}}.*?(?=^\\section\{{|\Z)",
            main,
            re.MULTILINE | re.DOTALL,
        )
        if match is None or "\\begin{promptbox}" not in match.group(0):
            errors.append(f"main.tex early section lacks a contextual prompt block: {section_name}")
    references = next((text for name, text in texts if name == "references.bib"), "")
    bib_keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", references))
    citations = {key.strip() for group in re.findall(r"\\(?:cite|nocite)(?:\w*)?\{([^}]*)\}", main) for key in group.split(",") if key.strip()}
    for key in sorted(citations - bib_keys):
        errors.append(f"bibliography citation key {key} is not defined in references.bib")
    for key in sorted(bib_keys - citations):
        errors.append(f"bibliography key {key} is not cited by tutorial")
    if "\\bibliography" not in main or not citations:
        errors.append("tutorial must include source references/citations")
    for name, text in texts:
        if re.search(r"curation\s+pending", text, re.I):
            errors.append(f"{name}: contains CURATION PENDING")
        if PRIVATE_PATH.search(text):
            errors.append(f"{name}: contains a private absolute path")
        if SECRET.search(text):
            errors.append(f"{name}: contains a possible secret")
        if any(pattern.search(text) for pattern in PRIVACY_PATTERNS):
            errors.append(f"{name}: privacy pattern appears in student-facing content")
        for label, pattern in LEGACY_PATTERNS.items():
            for line in text.splitlines():
                match = pattern.search(line)
                if match is None or _is_explicit_legacy_context(line, match):
                    continue
                if label == "obsolete hourly submission field" and not re.search(r"\b(?:current|submission|csv|header|column|field|hourly|retired|not\s+contain|no\s+.*(?:field|column))\b", line, re.I):
                    continue
                errors.append(f"{name}: current submission/legacy {label} appears without an explicit historical/rejection context")


def audit(root: Path, *, allow_unbuilt: bool = False) -> list[str]:
    errors: list[str] = []
    root = root.resolve()
    texts = _read_student_texts(root, errors)
    registry = _check_registry(root, errors)
    _check_coverage(root, registry, texts, errors, allow_unbuilt=allow_unbuilt)
    _check_content_contract(root, texts, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tutorial-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--allow-unbuilt",
        action="store_true",
        help="development-only: audit source artifacts before generated/transcripts.tex exists",
    )
    args = parser.parse_args(argv)
    errors = audit(args.tutorial_root, allow_unbuilt=args.allow_unbuilt)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("audited 13 prompt/transcript pairs, 7 notebooks, and 7 figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
