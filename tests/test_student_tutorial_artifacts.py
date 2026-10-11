import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_ROOT = REPO_ROOT / "docs" / "student_tutorial"
REGISTRY = TUTORIAL_ROOT / "prompt_registry.json"

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


def test_tutorial_registry_lists_all_required_prompt_ids():
    registry = json.loads(REGISTRY.read_text())

    assert [item["id"] for item in registry["prompts"]] == REQUIRED_PROMPT_IDS


def test_registry_entries_have_stable_deferred_artifact_paths():
    registry = json.loads(REGISTRY.read_text())

    for item in registry["prompts"]:
        assert isinstance(item["module"], str) and item["module"]
        assert isinstance(item["prompt_file"], str) and item["prompt_file"]
        assert isinstance(item["transcript_file"], str) and item["transcript_file"]
        assert isinstance(item["notebook_files"], list)
        assert isinstance(item["required_sources"], list)
        assert item["required_sources"]
        assert isinstance(item["verification_requirements"], list)
        assert item["verification_requirements"]
        assert item["privacy_level"] == EXPECTED_PRIVACY_LEVEL
        assert item["prompt_file"].startswith("prompts/")
        assert item["transcript_file"].startswith("transcripts/")
        assert all(path.startswith("notebooks/") for path in item["notebook_files"])


def test_deferred_prompt_and_transcript_files_are_not_required_in_task_one():
    registry = json.loads(REGISTRY.read_text())

    deferred_paths = [
        TUTORIAL_ROOT / item["prompt_file"]
        for item in registry["prompts"]
    ] + [
        TUTORIAL_ROOT / item["transcript_file"]
        for item in registry["prompts"]
    ]

    assert len(deferred_paths) == len(REQUIRED_PROMPT_IDS) * 2
