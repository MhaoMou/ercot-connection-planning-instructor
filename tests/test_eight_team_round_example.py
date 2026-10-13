from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs/instructor_manual/examples/eight_team_round"
SUBMISSIONS = EXAMPLE / "submissions"
ARTIFACT = ROOT / "data/processed/activsg2000_scenarios_v1/2028/jan_jun"
CANONICAL_COLUMNS = (
    "team_id",
    "site_id",
    "bus_id",
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_eight_team_fixture_has_current_site_level_contract() -> None:
    bus_ids = {
        row["bus_id"]
        for row in csv.DictReader((ARTIFACT / "bus.csv").open(newline="", encoding="utf-8"))
    }
    paths = sorted(SUBMISSIONS.glob("team-*.csv"))
    assert [path.name for path in paths] == [f"team-{index:02d}.csv" for index in range(1, 9)]
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            assert tuple(reader.fieldnames or ()) == CANONICAL_COLUMNS
            rows = list(reader)
        assert len(rows) == 2
        assert len({row["site_id"] for row in rows}) == 2
        assert {row["team_id"] for row in rows} == {path.stem}
        for row in rows:
            assert row["bus_id"] in bus_ids
            connection = float(row["connection_capacity_mw"])
            generator = float(row["generator_capacity_mw"])
            storage_power = float(row["storage_power_mw"])
            storage_energy = float(row["storage_energy_mwh"])
            assert 0 <= generator <= connection
            assert storage_power >= 0
            assert storage_energy == 4 * storage_power


def test_eight_team_report_matches_team_rows_and_aggregate() -> None:
    report = json.loads((EXAMPLE / "results.json").read_text(encoding="utf-8"))
    teams = report["teams"]
    assert len(teams) == 8
    assert [team["rank"] for team in teams] == list(range(1, 9))
    assert report["accepted_submission_count"] == 8
    assert report["rejected_submission_count"] == 0
    assert report["competition_scope"] == "joint"
    assert report["joint_dispatch_feasible"] is True
    assert report["projection_activated"] is False
    assert report["aggregate"]["connected_capacity_mw"] == sum(
        team["connected_capacity_mw"] for team in teams
    )
    assert report["aggregate"]["served_load_mwh"] == sum(
        team["served_load_mwh"] for team in teams
    )
    assert abs(report["aggregate"]["score"] - sum(team["score"] for team in teams)) < 1e-6
