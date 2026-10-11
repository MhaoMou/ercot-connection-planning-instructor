#!/usr/bin/env python3
"""Build the student-tutorial PDF from checked-in, offline inputs."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TUTORIAL_ROOT = SCRIPT_DIR.parent
LATEXMK = "latexmk"
PDFLATEX = "pdflatex"


def run_command(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def run_input_builder(tutorial_root: Path, generated_dir: Path) -> None:
    run_command(
        [
            sys.executable,
            str(tutorial_root / "scripts" / "build_latex_inputs.py"),
            "--output-dir",
            str(generated_dir),
        ],
        cwd=tutorial_root,
    )


def _toolchain() -> str:
    if shutil.which(LATEXMK):
        return LATEXMK
    if shutil.which(PDFLATEX):
        return PDFLATEX
    raise RuntimeError("no local LaTeX toolchain found (need latexmk or pdflatex)")


def build(*, tutorial_root: Path = DEFAULT_TUTORIAL_ROOT) -> Path:
    tutorial_root = tutorial_root.resolve()
    generated_dir = tutorial_root / "generated"
    build_dir = tutorial_root / "build"
    run_input_builder(tutorial_root, generated_dir)
    generated_input = generated_dir / "transcripts.tex"
    if not generated_input.is_file() or generated_input.stat().st_size == 0:
        raise RuntimeError(f"required generated input is missing or empty: {generated_input}")
    main_tex = tutorial_root / "main.tex"
    if not main_tex.is_file():
        raise RuntimeError(f"LaTeX source is missing: {main_tex}")
    build_dir.mkdir(parents=True, exist_ok=True)
    tool = _toolchain()
    if tool == LATEXMK:
        run_command(
            [tool, "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={build_dir}", str(main_tex)],
            cwd=tutorial_root,
        )
    else:
        for _ in range(2):
            run_command(
                [tool, "-interaction=nonstopmode", "-halt-on-error", "-output-directory", str(build_dir), str(main_tex)],
                cwd=tutorial_root,
            )
        bibtex = shutil.which("bibtex")
        if bibtex:
            run_command([bibtex, str(build_dir / "main")], cwd=build_dir)
            for _ in range(2):
                run_command(
                    [tool, "-interaction=nonstopmode", "-halt-on-error", "-output-directory", str(build_dir), str(main_tex)],
                    cwd=tutorial_root,
                )
    return build_dir / "main.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tutorial-root", type=Path, default=DEFAULT_TUTORIAL_ROOT)
    args = parser.parse_args()
    output = build(tutorial_root=args.tutorial_root)
    print(f"built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
