import ast
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
    source = notebook_source()
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


def notebook_source() -> str:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )


def notebook_code_source() -> str:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") == "code"
    )


def test_notebook_measures_sampled_native_rss_and_records_limits() -> None:
    source = notebook_source()
    for marker in (
        "threading.Thread",
        "rss_sampling_interval_s",
        "rss_sampling_limitation",
        "process_rss_peak_mb",
        "finally",
    ):
        assert marker in source


def test_notebook_runs_all_storage_horizons_by_default_and_records_explicit_skips() -> None:
    source = notebook_source()
    assert "STORAGE_MAX_HORIZON = None" in source
    assert "storage horizons are skipped only by explicit configuration" in source
    assert "storage horizon exceeds STORAGE_MAX_HORIZON" in source
    assert 'run_status": "skipped"' in source


def test_notebook_uses_prepared_nameplate_inputs_and_documents_solver_path() -> None:
    source = notebook_source()
    assert "load_prepared_artifact" in source
    assert "nameplate" in source
    assert "solve_dcopf" in source
    assert "generator_availability" not in source


def test_notebook_checkpoints_each_case_and_resumes_by_case_key() -> None:
    source = notebook_source()
    for marker in (
        "load_checkpoint",
        "write_checkpoint",
        "case_key",
        "resume",
        "to_csv",
        "os.replace",
    ):
        assert marker in source


def test_recommendation_requires_optimal_solver_status() -> None:
    source = notebook_source()
    assert 'row["solver_status"] == "optimal"' in source
    assert "dependency_limited_unverified" in source


def test_recommendation_marks_non_optimal_measurements_unverified() -> None:
    tree = ast.parse(notebook_code_source())
    recommendation_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "recommend"
    )
    namespace = {
        "WALL_TIME_BUDGET_S": 1800.0,
        "PEAK_RSS_BUDGET_MB": 12000.0,
    }
    exec(compile(ast.Module([recommendation_node], type_ignores=[]), "<recommend>", "exec"), namespace)
    recommend = namespace["recommend"]

    non_optimal = recommend([{
        "horizon_hours": 4416,
        "variant": "sparse_reuse",
        "run_status": "completed",
        "solver_status": "empty",
    }])
    assert non_optimal["recommendation"] == "dependency_limited_unverified"

    optimal = recommend([{
        "horizon_hours": 4416,
        "variant": "sparse_reuse",
        "run_status": "completed",
        "solver_status": "optimal",
        "median_wall_time_s": 1.0,
        "peak_memory_mb": 1.0,
    }])
    assert optimal["recommendation"] == "retain_official_4416_hour_horizon"


def test_benchmark_docs_warn_against_unrun_results_and_preserve_official_horizon() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    docs = DOC_PATH.read_text(encoding="utf-8")
    combined = f"{readme}\n{docs}"
    assert "Do not" in combined and "claim" in combined and "results" in combined
    assert "4,416" in combined or "4416" in combined
    assert "student" in combined.lower() and "screen" in combined.lower()
    assert "highspy" in combined
