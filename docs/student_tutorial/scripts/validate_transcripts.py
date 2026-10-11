#!/usr/bin/env python3
"""Validate frozen tutorial transcript metadata without calling a model."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath


TUTORIAL_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = TUTORIAL_ROOT / "prompt_registry.json"
REQUIRED_KEYS = {
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
REGISTRY_ENTRY_KEYS = {
    "id",
    "module",
    "prompt_file",
    "transcript_file",
    "notebook_files",
    "required_sources",
    "verification_requirements",
    "privacy_level",
}
PRIVACY_LEVEL = "public-or-instructor-approved-fixture"


def _repository_relative_file(
    value: object, repository_root: Path, *, require_file: bool = True
) -> Path:
    """Return a safe, existing repository file, rejecting symlink escapes."""
    if not isinstance(value, str) or not value or value.endswith(("/", "\\")):
        raise ValueError(f"source must be a non-empty file path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or "\\" in value
        or ".." in posix.parts
    ):
        raise ValueError(f"source path must be repository-relative and safe: {value!r}")
    root = repository_root.resolve()
    path = repository_root.joinpath(*posix.parts)
    resolved = path.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"source path escapes repository root: {value!r}")
    if require_file and not resolved.is_file():
        raise ValueError(f"source file does not exist: {value!r}")
    return resolved


def _tutorial_relative_file(
    value: object,
    expected_root: str,
    tutorial_root: Path,
    *,
    require_file: bool = True,
) -> Path:
    if (
        not isinstance(value, str)
        or not value.startswith(expected_root)
        or PurePosixPath(value).parts[:1] != (expected_root.rstrip("/"),)
    ):
        raise ValueError(f"path must start with {expected_root!r}: {value!r}")
    return _repository_relative_file(value, tutorial_root, require_file=require_file)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_placeholder_response(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return normalized in {
        "todo",
        "tbd",
        "pending",
        "curation pending",
        "placeholder",
        "[placeholder]",
        "[response pending]",
    } or "curation pending" in normalized


def _load_json(path: Path, label: str, errors: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: cannot read JSON: {exc}")
        return None


def _validate_registry(registry: object, errors: list[str]) -> list[dict]:
    if not isinstance(registry, dict):
        errors.append("registry must be an object")
        return []
    if set(registry) != {"schema_version", "prompts"}:
        errors.append("registry keys must be exactly schema_version and prompts")
    version = registry.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        errors.append("registry schema_version must be integer 1")
    prompts = registry.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        errors.append("registry prompts must be a non-empty list")
        return []
    entries: list[dict] = []
    ids: set[str] = set()
    for index, entry in enumerate(prompts):
        label = f"registry prompt {index}"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        if set(entry) != REGISTRY_ENTRY_KEYS:
            errors.append(f"{label} keys do not match schema")
        for key in ("id", "module", "prompt_file", "transcript_file", "privacy_level"):
            if not _non_empty_text(entry.get(key)):
                errors.append(f"{label}: {key} must be non-empty text")
        prompt_id = entry.get("id")
        if isinstance(prompt_id, str):
            if prompt_id in ids:
                errors.append(f"{label}: duplicate id {prompt_id!r}")
            ids.add(prompt_id)
        for key in ("notebook_files", "required_sources", "verification_requirements"):
            value = entry.get(key)
            if not isinstance(value, list):
                errors.append(f"{label}: {key} must be a list")
            elif key != "notebook_files" and not value:
                errors.append(f"{label}: {key} must be non-empty")
            elif not all(_non_empty_text(item) for item in value):
                errors.append(f"{label}: {key} entries must be non-empty text")
        if entry.get("privacy_level") != PRIVACY_LEVEL:
            errors.append(f"{label}: privacy_level is not the approved value")
        entries.append(entry)
    return entries


def _validate_transcript(
    transcript: object,
    prompt_id: object,
    required_sources: object,
    repository_root: Path,
    errors: list[str],
) -> None:
    label = str(prompt_id)
    if not isinstance(transcript, dict):
        errors.append(f"{label}: transcript must be an object")
        return
    if set(transcript) != REQUIRED_KEYS:
        errors.append(f"{label}: transcript keys do not match schema")
    if transcript.get("prompt_id") != prompt_id:
        errors.append(f"{label}: prompt_id mismatch")
    for key in ("prompt", "response", "agent_label", "generated_at", "review_notes"):
        if not _non_empty_text(transcript.get(key)):
            errors.append(f"{label}: {key} must be non-empty text")
    response = transcript.get("response")
    if isinstance(response, str) and _is_placeholder_response(response):
        errors.append(f"{label}: response must not be a placeholder")
    checklist = transcript.get("verification_checklist")
    if not isinstance(checklist, list) or not checklist:
        errors.append(f"{label}: verification_checklist must be a non-empty list")
    elif not all(_non_empty_text(item) for item in checklist):
        errors.append(f"{label}: verification_checklist entries must be non-empty text")
    sources = transcript.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{label}: sources must be a non-empty list")
    elif not all(_non_empty_text(source) for source in sources):
        errors.append(f"{label}: sources entries must be non-empty text")
    else:
        for source in sources:
            try:
                _repository_relative_file(source, repository_root)
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
            if isinstance(required_sources, list) and source not in required_sources:
                errors.append(
                    f"{label}: transcript source is not listed in registry required_sources: {source!r}"
                )
    if not isinstance(required_sources, list) or not required_sources:
        errors.append(f"{label}: required_sources must be a non-empty list")
    elif not all(_non_empty_text(source) for source in required_sources):
        errors.append(f"{label}: required_sources entries must be non-empty text")
    else:
        for source in required_sources:
            try:
                _repository_relative_file(source, repository_root)
            except ValueError as exc:
                errors.append(f"{label}: invalid required source: {exc}")
        if isinstance(sources, list):
            missing = [source for source in required_sources if source not in sources]
            if missing:
                errors.append(f"{label}: transcript sources omit required_sources: {missing!r}")
    if transcript.get("review_status") != "fact-checked":
        errors.append(f"{label}: review_status must be fact-checked")
    if transcript.get("private_information_present") is not False:
        errors.append(f"{label}: private_information_present must be false")
    generated_at = transcript.get("generated_at")
    if isinstance(generated_at, str):
        try:
            date.fromisoformat(generated_at)
        except ValueError:
            errors.append(f"{label}: generated_at must be YYYY-MM-DD")


def validate_transcripts(path: Path = REGISTRY_PATH) -> int:
    errors: list[str] = []
    registry = _load_json(path, "registry", errors)
    if registry is None:
        return 1
    entries = _validate_registry(registry, errors)
    repository_root = TUTORIAL_ROOT.parent.parent
    for entry in entries:
        prompt_id = entry.get("id")
        try:
            transcript_path = _tutorial_relative_file(
                entry.get("transcript_file"), "transcripts/", TUTORIAL_ROOT
            )
        except ValueError as exc:
            errors.append(f"{prompt_id}: invalid transcript_file: {exc}")
            continue
        try:
            _tutorial_relative_file(entry.get("prompt_file"), "prompts/", TUTORIAL_ROOT)
        except ValueError as exc:
            errors.append(f"{prompt_id}: invalid prompt_file: {exc}")
        notebook_files = entry.get("notebook_files")
        if not isinstance(notebook_files, list):
            notebook_files = []
        for notebook in notebook_files:
            try:
                _tutorial_relative_file(
                    notebook, "notebooks/", TUTORIAL_ROOT
                )
            except ValueError as exc:
                errors.append(f"{prompt_id}: invalid notebook_file: {exc}")
        transcript = _load_json(transcript_path, str(prompt_id), errors)
        if transcript is not None:
            _validate_transcript(
                transcript,
                prompt_id,
                entry.get("required_sources"),
                repository_root,
                errors,
            )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated {len(entries)} transcripts")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_transcripts())
