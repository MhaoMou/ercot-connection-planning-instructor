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


def _repository_relative_file(value: object, repository_root: Path) -> Path:
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
    path = repository_root.joinpath(*posix.parts)
    if not path.is_file():
        raise ValueError(f"source file does not exist: {value!r}")
    return path


def validate_transcripts(path: Path = REGISTRY_PATH) -> int:
    errors: list[str] = []
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 1

    entries = registry.get("prompts", [])
    repository_root = TUTORIAL_ROOT.parent.parent
    for entry in entries:
        prompt_id = entry.get("id")
        transcript_path = TUTORIAL_ROOT / entry["transcript_file"]
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{prompt_id}: cannot read transcript: {exc}")
            continue
        if set(transcript) != REQUIRED_KEYS:
            errors.append(f"{prompt_id}: transcript keys do not match schema")
        if transcript.get("prompt_id") != prompt_id:
            errors.append(f"{prompt_id}: prompt_id mismatch")
        for key in ("prompt", "response", "agent_label", "review_notes"):
            if not isinstance(transcript.get(key), str) or not transcript[key].strip():
                errors.append(f"{prompt_id}: {key} must be non-empty text")
        if not isinstance(transcript.get("verification_checklist"), list) or not transcript["verification_checklist"]:
            errors.append(f"{prompt_id}: verification_checklist must be non-empty")
        sources = transcript.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{prompt_id}: sources must be non-empty")
        else:
            for source in sources:
                try:
                    _repository_relative_file(source, repository_root)
                except ValueError as exc:
                    errors.append(f"{prompt_id}: {exc}")
        required_sources = entry.get("required_sources")
        if not isinstance(required_sources, list) or not required_sources:
            errors.append(f"{prompt_id}: required_sources must be non-empty")
        else:
            for source in required_sources:
                try:
                    _repository_relative_file(source, repository_root)
                except ValueError as exc:
                    errors.append(f"{prompt_id}: invalid required source: {exc}")
            if isinstance(sources, list):
                missing = [source for source in required_sources if source not in sources]
                if missing:
                    errors.append(
                        f"{prompt_id}: transcript sources omit required_sources: {missing!r}"
                    )
        if transcript.get("review_status") != "fact-checked":
            errors.append(f"{prompt_id}: review_status must be fact-checked")
        if transcript.get("private_information_present") is not False:
            errors.append(f"{prompt_id}: private_information_present must be false")
        try:
            date.fromisoformat(transcript.get("generated_at", ""))
        except (TypeError, ValueError):
            errors.append(f"{prompt_id}: generated_at must be YYYY-MM-DD")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated {len(entries)} transcripts")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_transcripts())
