from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
README = REPOSITORY_ROOT / "docs" / "hosting_capacity" / "README.md"
ARTIFACT_PATH = "data/processed/activsg2000_scenarios_v1/2028/jan_jun"


def test_hosting_capacity_readme_matches_current_output_and_benchmark_contracts():
    text = README.read_text(encoding="utf-8")

    assert text.count(f"--artifact-dir {ARTIFACT_PATH}") == 2
    assert "published NumPy surface is" in text
    assert "limited to capacity and status" in text
    assert "there is no `hosting_capacity_objective.npy`" in text
    assert "in-memory result and checkpoint `.npz` files" in text

    assert "incomplete contents in a well-named checkpoint are ignored and recomputed" in text
    assert "Invalid checkpoint filenames are rejected as request-identity errors" in text
    assert "well-formed filenames for periods outside the request" in text

    assert "backend.requested" in text
    assert "backend.resolved" in text
    assert "backend.version" in text
    for field in (
        "wall_time_s",
        "peak_rss_mb",
        "model_build_time_s",
        "mean_point_time_s",
        "p50_point_time_s",
        "p95_point_time_s",
        "completed_points",
        "failed_points",
    ):
        assert f"`{field}`" in text
    assert "reported as unavailable" in text
    assert "failure elapsed time as a benchmark timing" in text

    assert "capacity and objective entries are `NaN` in the\nNumPy outputs" not in text
    assert "Malformed or incomplete checkpoint files are ignored" not in text
