import json
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "docs" / "student_tutorial" / "prompt_registry.json"

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


def _assert_relative_artifact_path(path, expected_root):
    assert isinstance(path, str) and path
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
        "prompts/",
    ],
)
def test_registry_path_helper_rejects_windows_or_directory_paths(path):
    with pytest.raises(AssertionError):
        _assert_relative_artifact_path(path, EXPECTED_PROMPT_ROOT)


def test_registry_schema_version_rejects_bool():
    with pytest.raises(AssertionError):
        _assert_schema_version(True)
