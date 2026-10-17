import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
README = REPOSITORY_ROOT / "docs" / "hosting_capacity" / "README.md"
BENCHMARK_RESULTS = REPOSITORY_ROOT / "docs" / "hosting_capacity" / "benchmark-results.jsonl"
BENCHMARK_REPORT = REPOSITORY_ROOT / "docs" / "hosting_capacity" / "benchmark-report.md"
ARTIFACT_PATH = "data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun"
PILOT_GATE_HEADING = "## Task 9 release pilot gate"
PILOT_OUTPUT_ROOT = "/private/tmp/hosting-capacity-pilot"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    remainder = text[start:]
    next_heading = remainder.find("\n## ", len(heading))
    return remainder if next_heading == -1 else remainder[:next_heading]


def test_hosting_capacity_readme_matches_current_output_and_benchmark_contracts():
    text = README.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert text.count(f"--artifact-dir {ARTIFACT_PATH}") == 6
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
        "rss_delta_mb",
        "model_build_time_s",
        "mean_point_time_s",
        "p50_point_time_s",
        "p95_point_time_s",
        "resume_status",
        "completed_points",
        "failed_points",
    ):
        assert f"`{field}`" in text
    assert (
        "The default `auto` selection prefers Gurobi when available and falls back "
        "to portable HiGHS through the required project dependency `highspy`."
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
    assert (
        "`wall_time_s` is the elapsed time around the complete matrix-driver call. It may include "
        "provider materialization, valid-checkpoint loading, point solving, checkpoint writes, and "
        "output publication; it is not solve-only time unless a separate solve-only timer is instrumented."
    ) in normalized
    assert (
        "`model_build_time_s` is explicitly `null` because model construction occurs inside the matrix "
        "driver's private period solver and is not separately instrumented"
    ) in normalized
    assert (
        "`peak_rss_mb` is the legacy absolute post-stage `ru_maxrss` value in MiB, using the larger "
        "available value from the benchmark process and waited-for child processes"
    ) in normalized
    assert (
        "`rss_delta_mb` is the additive stage-local measurement: the maximum non-negative self/child "
        "delta between resource snapshots immediately before and after the stage"
    ) in normalized
    assert "child usage is not computed by summing lifetime counters" in normalized
    assert (
        "`resume_status` is `fresh` when no valid request-matching checkpoint was loaded and `resumed` "
        "when at least one was loaded, including an already-complete rerun."
    ) in normalized

    assert "capacity and objective entries are `NaN` in the\nNumPy outputs" not in text
    assert "Malformed or incomplete checkpoint files are ignored" not in text


def test_release_benchmark_evidence_contains_only_the_actual_ieee14_row():
    rows = [
        json.loads(line)
        for line in BENCHMARK_RESULTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows == [
        {
            "backend": "highs",
            "completed_points": 14,
            "failed_points": 14,
            "mean_point_time_s": 0.000103,
            "model_build_time_s": None,
            "p50_point_time_s": 0.000103,
            "p95_point_time_s": 0.000103,
            "peak_rss_mb": 33.797,
            "resume_status": "fresh",
            "rss_delta_mb": 0.609,
            "solver_threads": 1,
            "stage": "ieee14-1",
            "wall_time_s": 0.007359,
            "workers": 1,
        }
    ]

    report = BENCHMARK_REPORT.read_text(encoding="utf-8")
    combined = " ".join((README.read_text(encoding="utf-8"), report)).lower()
    assert "2026-10-15" in combined
    assert "dependency-limited" in combined
    assert "highspy" in combined
    assert "validation_status=unverified_dependency" in combined
    assert "24-period, 168-period, and full-horizon stages were not run" in combined
    assert "activsg2000-24" not in report
    assert "activsg2000-168" not in report
    assert "activsg2000-full" not in report
    assert PILOT_OUTPUT_ROOT in combined


def test_task9_pilot_gate_is_identical_and_decision_complete():
    readme = README.read_text(encoding="utf-8")
    report = BENCHMARK_REPORT.read_text(encoding="utf-8")
    readme_gate = _section(readme, PILOT_GATE_HEADING)
    report_gate = _section(report, PILOT_GATE_HEADING)
    normalized_gate = " ".join(readme_gate.split()).lower()

    assert " ".join(readme_gate.split()) == " ".join(report_gate.split())
    assert "--artifact-dir " + ARTIFACT_PATH in readme_gate
    assert "--artifact-dir " + ARTIFACT_PATH in report_gate
    assert "--bus-id 1001" in readme_gate
    assert readme_gate.count("--bus-id 1001") >= 2
    for bus_id in range(1001, 1011):
        assert f"--bus-id {bus_id}" in readme_gate
    assert "--output-dir /private/tmp/hosting-capacity-pilot/one-bus-24h" in readme_gate
    assert "--output-dir /private/tmp/hosting-capacity-pilot/ten-bus-24h" in readme_gate
    assert "--output-dir /private/tmp/hosting-capacity-pilot/all-bus-1h" in readme_gate
    assert PILOT_OUTPUT_ROOT in readme_gate

    for shape in ("(24, 1)", "(24, 10)", "(1, 2000)"):
        assert shape in readme_gate
    for phrase in (
        "every command has exit code 0",
        "resume_status=fresh",
        "resume_status=resumed",
        "valid completed checkpoint",
        "no malformed",
        "wall_time_s",
        "peak_rss_mb",
        "rss_delta_mb",
        "explicit operator approval",
        "before any full-horizon",
        "every matrix status is non-empty and `optimal`",
    ):
        assert phrase in normalized_gate

    lowered = readme_gate.lower()
    assert "optional pilot" not in lowered
    assert "pilot is optional" not in lowered
    assert "pilots are optional" not in lowered
    assert "should run" not in lowered
    assert "may run" not in lowered


def test_report_calls_highspy_required_and_unavailable_here():
    report = BENCHMARK_REPORT.read_text(encoding="utf-8").lower()

    assert "highspy" in report
    assert "required project dependency" in report
    assert "highspy" in report and "unavailable in this environment" in report
    assert "unavailable optional" not in report


def test_hosting_capacity_results_are_narrowly_ignored():
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "results/hosting_capacity_*/" in gitignore
    assert "results/hosting_capacity_benchmark_*/" in gitignore
    assert "results/" in gitignore
    assert "docs/hosting_capacity/" not in gitignore
