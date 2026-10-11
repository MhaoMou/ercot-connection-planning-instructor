#!/usr/bin/env python3
"""Execute the student tutorial notebooks in a deterministic clean process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


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

    output_dir.mkdir(parents=True, exist_ok=True)
    tutorial_root = input_dir.parent.resolve()
    repository_root = tutorial_root.parent.parent.resolve()
    artifact_root = output_dir.parent.resolve()
    with tempfile.TemporaryDirectory(prefix="student-tutorial-", dir=output_dir.parent) as clean_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "TUTORIAL_ROOT": str(tutorial_root),
                "REPO_ROOT": str(repository_root),
                "TUTORIAL_OUTPUT_ROOT": str(artifact_root),
                "MPLCONFIGDIR": str(Path(clean_dir) / "mplconfig"),
                "PYTHONPATH": os.pathsep.join(
                    [str(repository_root / "src"), str(repository_root), environment.get("PYTHONPATH", "")]
                ),
            }
        )
        os.environ.update(environment)
        summaries: list[str] = []
        for source in notebooks:
            destination = output_dir / source.name
            notebook = nbformat.read(source, as_version=4)
            client = NotebookClient(
                notebook,
                timeout=timeout,
                kernel_name="python3",
                resources={"metadata": {"path": clean_dir}},
                allow_errors=False,
            )
            try:
                client.execute()
            except CellExecutionError as exc:
                raise SystemExit(f"notebook execution failed: {source.name}: {exc}") from exc
            nbformat.write(notebook, destination)
            summaries.append(source.name)
    print(f"executed {len(summaries)} notebooks")
    for name in summaries:
        print(f"  PASS {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return execute_notebooks(args.input_dir, args.output_dir, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
