#!/usr/bin/env python3
"""Validate frozen tutorial transcript metadata without calling a model."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path


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


def validate_transcripts(path: Path = REGISTRY_PATH) -> int:
    errors: list[str] = []
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 1

    entries = registry.get("prompts", [])
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
        if not isinstance(transcript.get("sources"), list) or not transcript["sources"]:
            errors.append(f"{prompt_id}: sources must be non-empty")
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
