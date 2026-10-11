#!/usr/bin/env python3
"""Validate the stable prompt registry and its referenced artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


TUTORIAL_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = TUTORIAL_ROOT / "prompt_registry.json"
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
REQUIRED_HEADINGS = [
    "# Prompt metadata",
    "## Goal",
    "## Context supplied to the AI",
    "## Prompt",
    "## Expected evidence",
    "## Privacy constraints",
]


def _relative_path(value: object, root: str) -> Path:
    if not isinstance(value, str) or not value or value.endswith(("/", "\\")):
        raise ValueError(f"invalid artifact path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"artifact path must be relative: {value!r}")
    if "\\" in value or ".." in posix.parts or not value.startswith(root):
        raise ValueError(f"artifact path is unsafe or incorrectly rooted: {value!r}")
    if not posix.parts or posix.parts[0] != root.rstrip("/"):
        raise ValueError(f"artifact path is unsafe or incorrectly rooted: {value!r}")
    tutorial_root = TUTORIAL_ROOT.resolve()
    resolved = (TUTORIAL_ROOT / Path(*posix.parts)).resolve(strict=False)
    if resolved != tutorial_root and tutorial_root not in resolved.parents:
        raise ValueError(f"artifact path escapes tutorial root: {value!r}")
    return resolved


def validate_registry(path: Path = REGISTRY_PATH) -> int:
    errors: list[str] = []
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 1

    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    entries = registry.get("prompts")
    if not isinstance(entries, list):
        errors.append("prompts must be a list")
        entries = []
    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if ids != REQUIRED_IDS:
        errors.append(f"prompt IDs must be exactly {REQUIRED_IDS!r}")
    if len(ids) != len(set(ids)):
        errors.append("prompt IDs must be unique")

    seen_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("each prompt entry must be an object")
            continue
        prompt_file = entry.get("prompt_file")
        transcript_file = entry.get("transcript_file")
        try:
            prompt_path = _relative_path(prompt_file, "prompts/")
            transcript_path = _relative_path(transcript_file, "transcripts/")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        for artifact in (prompt_file, transcript_file):
            if artifact in seen_paths:
                errors.append(f"duplicate artifact path: {artifact}")
            seen_paths.add(artifact)
        if not prompt_path.is_file():
            errors.append(f"missing prompt file: {prompt_file}")
        else:
            text = prompt_path.read_text(encoding="utf-8")
            for heading in REQUIRED_HEADINGS:
                if heading not in text:
                    errors.append(f"{prompt_file} missing heading: {heading}")
        if not transcript_path.is_file():
            errors.append(f"missing transcript file: {transcript_file}")
        for notebook_file in entry.get("notebook_files", []):
            try:
                notebook_path = _relative_path(notebook_file, "notebooks/")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not notebook_path.is_file():
                errors.append(f"missing notebook file: {notebook_file}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated {len(entries)} prompt/transcript pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_registry())
