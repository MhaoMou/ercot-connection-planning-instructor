#!/usr/bin/env python3
"""Audit the student-facing tutorial contract without calling a model.

The audit deliberately reads only the tutorial's student-facing artifacts. It
does not scan historical manuals, source code, or other repository documents,
so legacy terminology elsewhere cannot create a false positive here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_IDS = [
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
CANONICAL_HEADER = (
    "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,"
    "storage_power_mw,storage_energy_mwh"
)
EXPECTED_NOTEBOOKS = {
    "01_dataset_inspection.ipynb",
    "02_network_visualization.ipynb",
    "03_three_bus_dcopf.ipynb",
    "04_storage_soc.ipynb",
    "05_csv_validation.ipynb",
    "06_student_preview.ipynb",
    "07_strategy_ablation.ipynb",
}
EXPECTED_FIGURES = {
    "pipeline.tex",
    "data_relationships.tex",
    "student_vs_evaluator.tex",
    "three_bus_dcopf.tex",
    "csv_evaluation_flow.tex",
    "storage_soc.tex",
    "strategy_experiment.tex",
}
REQUIRED_PROMPT_HEADINGS = (
    "# Prompt metadata",
    "## Goal",
    "## Context supplied to the AI",
    "## Prompt",
    "## Expected evidence",
    "## Privacy constraints",
)
LEGACY_PATTERNS = {
    "six-period terminology": re.compile(r"\bsix[- ]period(?:s)?\b", re.I),
    "intervention field": re.compile(r"\bintervention\b", re.I),
    "curtailment field": re.compile(r"\bcurtailment\b", re.I),
    "path-corridor field": re.compile(r"\bpath[- ]corridor\b", re.I),
    "teaching-bus terminology": re.compile(r"\bteaching bus\b", re.I),
    "obsolete hourly submission field": re.compile(
        r"(?<![A-Za-z0-9_-])(?:period|load_mw|submission_id|generator_id|storage_id)(?![A-Za-z0-9_-])",
        re.I,
    ),
}
EXPLICIT_COMPARISON = re.compile(
    r"\b(?:historical|legacy|retired|obsolete|deprecated|comparison|warning)\b"
    r"|\b(?:reject(?:ed|s)?|not\s+(?:a\s+)?current|not\s+part\s+of|"
    r"not\s+(?:contain|include|use)|do\s+not\s+add|not\s+hourly|no\s+hourly)\b",
    re.I,
)
PRIVATE_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\|/private/var/)", re.I)
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_ -]?key|access[_ -]?token|password)\s*[:=])",
    re.I,
)


def _read_student_texts(root: Path) -> list[tuple[str, str]]:
    paths = [root / "README.md", root / "main.tex", root / "references.bib"]
    paths += sorted((root / "prompts").glob("*.md"))
    paths += sorted((root / "transcripts").glob("*.json"))
    paths += sorted((root / "figures").glob("*.tex"))
    texts: list[tuple[str, str]] = []
    for path in paths:
        if path.is_file():
            name = path.relative_to(root).as_posix()
            raw = path.read_text(encoding="utf-8")
            if path.suffix == ".json" and path.parent.name == "transcripts":
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    texts.append((name, raw))
                else:
                    parts = [record.get(key, "") for key in ("prompt", "response", "review_notes")]
                    parts += ["\n".join(record.get(key, [])) for key in ("verification_checklist", "sources")]
                    texts.append((name, "\n".join(str(part) for part in parts)))
            else:
                texts.append((name, raw))
    for path in sorted((root / "notebooks").glob("*.ipynb")):
        if not path.is_file():
            continue
        notebook = json.loads(path.read_text(encoding="utf-8"))
        markdown = "\n".join(
            str(cell.get("source", ""))
            for cell in notebook.get("cells", [])
            if cell.get("cell_type") == "markdown"
        )
        texts.append((path.relative_to(root).as_posix() + ":markdown", markdown))
    return texts


def _check_registry(root: Path, errors: list[str]) -> dict:
    registry_path = root / "prompt_registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"registry: cannot read JSON: {exc}")
        return {}
    entries = registry.get("prompts") if isinstance(registry, dict) else None
    ids = [entry.get("id") for entry in entries or [] if isinstance(entry, dict)]
    if ids != REQUIRED_IDS:
        errors.append(f"registry IDs must be exactly {REQUIRED_IDS!r}")
    if len(ids) != len(set(ids)):
        errors.append("registry IDs must be unique")
    for entry in entries or []:
        if not isinstance(entry, dict):
            errors.append("registry entry must be an object")
            continue
        prompt_id = entry.get("id", "<missing>")
        for key in ("prompt_file", "transcript_file"):
            value = entry.get(key)
            path = root / value if isinstance(value, str) else root / "__missing__"
            if not isinstance(value, str) or not path.is_file():
                errors.append(f"{prompt_id}: missing {key}")
        prompt_path = root / entry["prompt_file"] if isinstance(entry.get("prompt_file"), str) else None
        if prompt_path and prompt_path.is_file():
            prompt = prompt_path.read_text(encoding="utf-8")
            for heading in REQUIRED_PROMPT_HEADINGS:
                if heading not in prompt:
                    errors.append(f"{prompt_id}: prompt missing {heading}")
        transcript_path = root / entry["transcript_file"] if isinstance(entry.get("transcript_file"), str) else None
        if transcript_path and transcript_path.is_file():
            try:
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{prompt_id}: invalid transcript JSON: {exc}")
                continue
            if transcript.get("prompt_id") != prompt_id:
                errors.append(f"{prompt_id}: prompt/transcript ID mismatch")
            if transcript.get("review_status") != "fact-checked":
                errors.append(f"{prompt_id}: transcript is not fact-checked")
            if transcript.get("private_information_present") is not False:
                errors.append(f"{prompt_id}: transcript contains private information")
            if not transcript.get("prompt") or not transcript.get("response"):
                errors.append(f"{prompt_id}: prompt/response pair is incomplete")
            if not transcript.get("verification_checklist"):
                errors.append(f"{prompt_id}: verification checklist is missing")
    return registry


def _check_coverage(root: Path, registry: dict, texts: list[tuple[str, str]], errors: list[str]) -> None:
    entries = registry.get("prompts", []) if isinstance(registry, dict) else []
    registered_notebooks = {
        Path(path).name
        for entry in entries
        if isinstance(entry, dict)
        for path in entry.get("notebook_files", [])
        if isinstance(path, str)
    }
    missing_notebooks = EXPECTED_NOTEBOOKS - registered_notebooks
    if missing_notebooks:
        errors.append(f"registry does not cover notebooks: {sorted(missing_notebooks)}")
    actual_notebooks = {path.name for path in (root / "notebooks").glob("*.ipynb")}
    if actual_notebooks != EXPECTED_NOTEBOOKS:
        errors.append("tutorial must contain exactly the seven required notebooks")
    actual_figures = {path.name for path in (root / "figures").glob("*.tex")}
    if actual_figures != EXPECTED_FIGURES:
        errors.append("tutorial must contain exactly the seven required figures")
    main = next((text for name, text in texts if name == "main.tex"), "")
    for figure in EXPECTED_FIGURES:
        if f"figures/{Path(figure).stem}" not in main:
            errors.append(f"main.tex does not integrate figure {figure}")


def _check_content_contract(texts: list[tuple[str, str]], errors: list[str]) -> None:
    combined = "\n".join(text for _, text in texts)
    if CANONICAL_HEADER not in combined:
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
    if "\\bibliography" not in combined or "\\nocite" not in combined or not re.search(r"@\w+\{", combined):
        errors.append("tutorial must include source references/citations")
    for name, text in texts:
        if re.search(r"curation\s+pending", text, re.I):
            errors.append(f"{name}: contains CURATION PENDING")
        if PRIVATE_PATH.search(text):
            errors.append(f"{name}: contains a private absolute path")
        if SECRET.search(text):
            errors.append(f"{name}: contains a possible secret")
        for label, pattern in LEGACY_PATTERNS.items():
            for line in text.splitlines():
                if not pattern.search(line) or EXPLICIT_COMPARISON.search(line):
                    continue
                if label == "obsolete hourly submission field" and not re.search(
                    r"\b(?:current|submission|csv|header|column|field|hourly|retired|not\s+contain|no\s+.*(?:field|column))\b",
                    line,
                    re.I,
                ):
                    continue
                if pattern.search(line):
                    errors.append(
                        f"{name}: current submission/legacy {label} appears without an explicit "
                        "historical/rejection context"
                    )


def audit(root: Path) -> list[str]:
    errors: list[str] = []
    texts = _read_student_texts(root)
    registry = _check_registry(root, errors)
    _check_coverage(root, registry, texts, errors)
    _check_content_contract(texts, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tutorial-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = audit(args.tutorial_root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("audited 13 prompt/transcript pairs, 7 notebooks, and 7 figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
