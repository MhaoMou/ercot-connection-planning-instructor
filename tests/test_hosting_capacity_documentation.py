from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
README = REPOSITORY_ROOT / "docs" / "hosting_capacity" / "README.md"
ARTIFACT_PATH = "data/processed/activsg2000_scenarios_v1/2028/jan_jun"


def test_hosting_capacity_readme_matches_current_output_and_benchmark_contracts():
    text = README.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

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
    assert (
        "The default `auto` selection prefers licensed Gurobi and falls back to "
        "portable HiGHS when Gurobi is unavailable."
    ) in normalized
    assert "HiGHS is the portable default backend" not in normalized

    assert (
        "Solver/backend failures can produce a timed stage row, with failed points "
        "recorded in `failed_points`; the row still includes the timing fields."
    ) in normalized
    assert (
        "Artifact validation or other setup errors exit nonzero before the affected "
        "stage begins, so no stage row is written for that stage."
    ) in normalized
    assert "reported as unavailable" not in normalized
    assert "unavailable timing fields" not in normalized
    assert "pre-stage failure elapsed time as a benchmark timing" in normalized

    assert "capacity and objective entries are `NaN` in the\nNumPy outputs" not in text
    assert "Malformed or incomplete checkpoint files are ignored" not in text
