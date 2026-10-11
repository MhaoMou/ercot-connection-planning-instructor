#!/usr/bin/env python3
"""Build the student-tutorial PDF from checked-in, offline inputs."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TUTORIAL_ROOT = SCRIPT_DIR.parent
LATEXMK = "latexmk"
PDFLATEX = "pdflatex"
BIBTEX = "bibtex"

_BIBLIOGRAPHY_WARNING_RE = re.compile(
    r"(?:Warning--|I didn't find a database entry|undefined citation|undefined references|No file .*\.bbl)",
    re.IGNORECASE,
)


def run_command(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return (completed.stdout or "") + (completed.stderr or "")


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


def _latexmk_command(main_tex: Path, build_dir: Path) -> list[str]:
    return [
        LATEXMK,
        "-pdf",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={build_dir}",
        str(main_tex),
    ]


def _pdflatex_command(main_tex: Path, build_dir: Path) -> list[str]:
    return [
        PDFLATEX,
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory",
        str(build_dir),
        str(main_tex),
    ]


def _check_build_output(output: str) -> None:
    bibliography_match = _BIBLIOGRAPHY_WARNING_RE.search(output)
    if bibliography_match:
        raise RuntimeError(
            "bibliography warnings remain in the LaTeX build output: "
            + bibliography_match.group(0)
        )
    if re.search(r"Overfull \\[hv]box", output, re.IGNORECASE):
        raise RuntimeError("overfull boxes remain in the LaTeX build output")


def _read_build_logs(build_dir: Path) -> str:
    chunks = []
    for path in (build_dir / "main.log", build_dir / "main.blg"):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


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
    if not shutil.which(BIBTEX):
        raise RuntimeError(
            "required bibliography toolchain is unavailable: bibtex was not found"
        )
    if tool == LATEXMK:
        run_command(_latexmk_command(main_tex, build_dir), cwd=tutorial_root)
    else:
        for _ in range(2):
            run_command(_pdflatex_command(main_tex, build_dir), cwd=tutorial_root)
        run_command([BIBTEX, str(build_dir / "main")], cwd=tutorial_root)
        for _ in range(2):
            run_command(_pdflatex_command(main_tex, build_dir), cwd=tutorial_root)
    _check_build_output(_read_build_logs(build_dir))
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
