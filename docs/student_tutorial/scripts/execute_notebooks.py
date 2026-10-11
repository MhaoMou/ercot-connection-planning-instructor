#!/usr/bin/env python3
"""Execute the student tutorial notebooks in a deterministic clean process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError


PUBLISHED_ARTIFACTS = {
    "dataset_inspection.json",
    "network_structure.png",
    "generator_overlay.png",
    "demand_summary.png",
    "three_bus_dcopf.json",
    "storage_soc.json",
    "csv_validation.json",
    "student_preview.json",
    "strategy_ablation.csv",
    "strategy_ablation.png",
}


class IsolatedNotebookClient(NotebookClient):
    """Notebook client that passes a clean environment to its kernel."""

    def __init__(self, *args, kernel_env: dict[str, str], **kwargs):
        super().__init__(*args, **kwargs)
        self._kernel_env = kernel_env

    async def async_start_new_kernel(self, **kwargs):
        kwargs.setdefault("env", self._kernel_env)
        await super().async_start_new_kernel(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def _reject_overlapping_paths(input_dir: Path, output_dir: Path) -> None:
    try:
        input_dir.relative_to(output_dir)
        overlaps = True
    except ValueError:
        try:
            output_dir.relative_to(input_dir)
            overlaps = True
        except ValueError:
            overlaps = False
    if overlaps:
        raise SystemExit(f"input and output directories overlap: {input_dir} / {output_dir}")


def _normalize_paths(value: object, run_root: str) -> object:
    if isinstance(value, str):
        return value.replace(run_root, "<isolated-run-root>")
    if isinstance(value, list):
        return [_normalize_paths(item, run_root) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_paths(item, run_root)
            for key, item in value.items()
            if key != "execution"
        }
    return value


def _publish(staging_root: Path, output_dir: Path) -> None:
    artifact_root = output_dir.parent
    staged_notebooks = staging_root / "notebooks"
    staged_artifacts = staging_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.glob("*.ipynb"):
        path.unlink()
    for name in PUBLISHED_ARTIFACTS | {"fixtures"}:
        stale = artifact_root / name
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()
    for path in staged_notebooks.glob("*.ipynb"):
        shutil.copy2(path, output_dir / path.name)
    for path in staged_artifacts.iterdir():
        destination = artifact_root / path.name
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(path, destination)


def execute_notebooks(input_dir: Path, output_dir: Path, timeout: int = 120) -> int:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"input notebook directory does not exist: {input_dir}")
    notebooks = sorted(input_dir.glob("*.ipynb"))
    if not notebooks:
        raise SystemExit(f"input notebook directory contains no .ipynb files: {input_dir}")
    if timeout <= 0:
        raise SystemExit("timeout must be a positive integer")
    _reject_overlapping_paths(input_dir, output_dir)
    tutorial_root = input_dir.parent.resolve()
    repository_root = tutorial_root.parent.parent.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="student-tutorial-", dir=output_dir.parent) as clean_dir:
        run_root = Path(clean_dir).resolve()
        staging_notebooks = run_root / "notebooks"
        staging_artifacts = run_root / "artifacts"
        staging_notebooks.mkdir()
        staging_artifacts.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "TUTORIAL_ROOT": str(tutorial_root),
                "REPO_ROOT": str(repository_root),
                "TUTORIAL_OUTPUT_ROOT": str(staging_artifacts),
                "MPLCONFIGDIR": str(run_root / "mplconfig"),
                "PYTHONPATH": os.pathsep.join(
                    [str(repository_root / "src"), str(repository_root), environment.get("PYTHONPATH", "")]
                ),
            }
        )
        summaries: list[str] = []
        for source in notebooks:
            notebook = nbformat.read(source, as_version=4)
            client = IsolatedNotebookClient(
                notebook,
                timeout=timeout,
                kernel_name="python3",
                resources={"metadata": {"path": str(run_root)}},
                kernel_env=environment,
                allow_errors=False,
            )
            try:
                client.execute()
            except CellExecutionError as exc:
                raise SystemExit(f"notebook execution failed: {source.name}: {exc}") from exc
            normalized = nbformat.from_dict(_normalize_paths(notebook, str(run_root)))
            nbformat.write(normalized, staging_notebooks / source.name)
            summaries.append(source.name)
        _publish(run_root, output_dir)
    print(f"executed {len(summaries)} notebooks")
    for name in summaries:
        print(f"  PASS {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return execute_notebooks(args.input_dir, args.output_dir, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
