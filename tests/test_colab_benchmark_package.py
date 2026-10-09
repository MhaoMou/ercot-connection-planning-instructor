import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
NOTEBOOK_PATH = ROOT / "experiments/colab/half_year_dcopf_benchmark.ipynb"
README_PATH = ROOT / "experiments/colab/README.md"
DOC_PATH = ROOT / "docs/COLAB_DCOPF_BENCHMARK.md"


def test_colab_benchmark_package_files_are_present() -> None:
    assert NOTEBOOK_PATH.is_file()
    assert README_PATH.is_file()
    assert DOC_PATH.is_file()


def test_notebook_declares_horizons_and_reproducibility_contract() -> None:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )
    for horizon in (1, 24, 168, 720, 4368, 4416):
        assert str(horizon) in source
    for marker in (
        "highspy",
        "tracemalloc",
        "wall_time_s",
        "peak_memory_mb",
        "variable_count",
        "constraint_count",
        "solver_status",
        "serial",
        "sparse_reuse",
        "chunked_parallel",
        "storage",
        "no_storage",
        "P95",
    ):
        assert marker in source


def test_benchmark_docs_warn_against_unrun_results_and_preserve_official_horizon() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    docs = DOC_PATH.read_text(encoding="utf-8")
    combined = f"{readme}\n{docs}"
    assert "Do not" in combined and "claim" in combined and "results" in combined
    assert "4,416" in combined or "4416" in combined
    assert "student" in combined.lower() and "screen" in combined.lower()
    assert "highspy" in combined
