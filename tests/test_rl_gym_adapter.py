from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import tomllib

import pytest

from ercot_game import batch_evaluation
from ercot_game.batch_evaluation import TeamScore
from ercot_game.calendars import half_year_calendar
from ercot_game.csv_submission import (
    TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH,
    TEAM_GENERATOR_COST_CURVE,
    read_student_csv,
)
from ercot_game.models import SitePlan
from tools.activsg2000.plan_adapter import (
    _normalize_plan_for_adapter,
    normalize_plan_for_adapter,
)
from rl_gym.adapter import (
    HalfYearHorizon,
    RLPlanState,
    compare_csv_parity,
    evaluate_plan_state,
)


def _sites() -> tuple[SitePlan, ...]:
    return (
        SitePlan("site-north", "3002041", 40.0, 20.0, 10.0, 40.0),
        SitePlan("site-south", "3002042", 15.0),
    )


def _score(**overrides: object) -> TeamScore:
    values = {
        "team_id": "team-a",
        "rank": 1,
        "valid": True,
        "feasible": True,
        "score": 123.5,
        "served_load_mwh": 55.0,
        "it_value": 82500.0,
        "energy_cost": 1200.0,
        "generator_operating_cost": 300.0,
        "generator_capital_cost": 26000000.0,
        "site_cost": 1100.0,
        "competition_scope": "joint",
        "storage_capital_cost": 5500000.0,
    }
    values.update(overrides)
    return TeamScore(**values)


def test_plan_state_preserves_half_year_metadata_and_materializes_evaluator_plan() -> None:
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2028,
        round_name="jan_jun",
    )

    assert state.horizon == HalfYearHorizon(2028, "jan_jun", 4368)
    assert state.horizon_hours == 4368

    plan = state.to_team_plan()
    assert tuple(plan.sites) == _sites()
    assert [(item.connection_id, item.profile) for item in plan.connections] == [
        ("site-north", (1.0,) * 4368),
        ("site-south", (1.0,) * 4368),
    ]
    assert [(item.generator_id, item.connection_id) for item in plan.generators] == [
        ("generator:team-a:site-north", "site-north"),
    ]
    assert plan.generators[0].marginal_cost_per_mwh == TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH
    assert TEAM_GENERATOR_COST_CURVE == ((0.25, 80.0), (0.50, 150.0), (0.25, 250.0))
    assert [(item.storage_id, item.connection_id) for item in plan.storages] == [
        ("storage:team-a:site-north", "site-north"),
    ]


def test_plan_materialization_is_deterministic_and_adapter_has_no_gym_step_api() -> None:
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jul_dec",
    )

    assert state.to_team_plan() == state.to_team_plan()
    assert not hasattr(state, "reset")
    assert not hasattr(state, "step")


def test_csv_parity_helper_compares_canonical_structure_and_evaluator_cost_fields(tmp_path) -> None:
    csv_path = tmp_path / "team-a.csv"
    csv_path.write_text(
        "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,"
        "storage_power_mw,storage_energy_mwh\n"
        "team-a,site-north,3002041,40,20,10,40\n"
        "team-a,site-south,3002042,15,0,0,0\n",
        encoding="utf-8",
    )
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jan_jun",
    )

    report = compare_csv_parity(
        state,
        csv_path,
        bus_ids={"3002041", "3002042"},
        adapter_score=_score(),
        csv_score=_score(),
    )

    assert report.canonical_structure_equal is True
    assert report.score_fields_equal is True
    assert report.cost_fields_equal is True
    assert report.differences == ()
    parsed = read_student_csv(
        csv_path,
        bus_ids={"3002041", "3002042"},
        horizon=state.horizon_hours,
    )
    assert report.adapter_plan == parsed.plan


def test_csv_parity_helper_reports_structure_and_score_mismatches(tmp_path) -> None:
    csv_path = tmp_path / "team-a.csv"
    csv_path.write_text(
        "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,"
        "storage_power_mw,storage_energy_mwh\n"
        "team-a,site-north,3002041,40,20,10,40\n",
        encoding="utf-8",
    )
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jan_jun",
    )

    report = compare_csv_parity(
        state,
        csv_path,
        bus_ids={"3002041", "3002042"},
        adapter_score=_score(),
        csv_score=_score(score=999.0, energy_cost=999.0),
    )

    assert report.canonical_structure_equal is False
    assert report.score_fields_equal is False
    assert report.cost_fields_equal is False
    assert "canonical_structure" in report.differences
    assert "score" in report.differences
    assert "energy_cost" in report.differences


def test_csv_parity_helper_accepts_batch_evaluation_score_containers(tmp_path) -> None:
    csv_path = tmp_path / "team-a.csv"
    csv_path.write_text(
        "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,"
        "storage_power_mw,storage_energy_mwh\n"
        "team-a,site-north,3002041,40,20,10,40\n"
        "team-a,site-south,3002042,15,0,0,0\n",
        encoding="utf-8",
    )
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jan_jun",
    )
    evaluation = SimpleNamespace(team_scores=(_score(),))

    report = compare_csv_parity(
        state,
        csv_path,
        bus_ids={"3002041", "3002042"},
        adapter_score=evaluation,
        csv_score=evaluation,
    )

    assert report.score_fields_equal is True
    assert report.cost_fields_equal is True


def test_evaluate_plan_state_delegates_and_preserves_caller_submission_path(tmp_path, monkeypatch) -> None:
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jan_jun",
    )
    calls: dict[str, object] = {}
    submission_path = tmp_path / "team-a.csv"
    expected = SimpleNamespace()

    def fake_evaluate(csv_paths, **kwargs):
        calls["path"] = csv_paths[0]
        calls["kwargs"] = kwargs
        assert csv_paths[0].is_file()
        expected.accepted_submissions = (SimpleNamespace(path=csv_paths[0]),)
        submission = read_student_csv(
            csv_paths[0],
            bus_ids={"3002041", "3002042"},
            horizon=state.horizon_hours,
        )
        assert submission.plan == state.to_team_plan()
        return expected

    monkeypatch.setattr(batch_evaluation, "evaluate_csv_batch", fake_evaluate)
    rules = object()

    result = evaluate_plan_state(
        state,
        submission_path=submission_path,
        artifact_dir=tmp_path,
        rules=rules,
    )

    assert result is expected
    assert result.accepted_submissions[0].path == submission_path
    assert result.accepted_submissions[0].path.is_file()
    assert submission_path.is_file()
    assert calls["kwargs"] == {
        "artifact_dir": tmp_path,
        "horizon": state.horizon_hours,
        "rules": rules,
    }
    assert calls["path"] == submission_path


@pytest.mark.parametrize("bad_horizon", [0, -1, True, 1.0, "4344"])
def test_half_year_horizon_requires_positive_non_bool_integer(bad_horizon: object) -> None:
    with pytest.raises(ValueError, match="horizon_hours must be a positive integer"):
        HalfYearHorizon(2027, "jan_jun", bad_horizon)  # type: ignore[arg-type]


def test_public_plan_normalizer_preserves_private_compatibility_alias() -> None:
    state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=_sites(),
        year=2027,
        round_name="jan_jun",
    )

    assert normalize_plan_for_adapter(state.to_team_plan(), horizon=1) == _normalize_plan_for_adapter(
        state.to_team_plan(), horizon=1
    )


@pytest.mark.parametrize("bad_team_id", ["team a", "team/a", " team-a"])
def test_plan_state_uses_canonical_public_id_constraints(bad_team_id: str) -> None:
    with pytest.raises(ValueError, match="malformed|canonical"):
        RLPlanState.for_half_year(
            team_id=bad_team_id,
            sites=_sites(),
            year=2027,
            round_name="jan_jun",
        )


def test_plan_state_rejects_empty_canonical_submission() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RLPlanState.for_half_year(
            team_id="team-a",
            sites=(),
            year=2027,
            round_name="jan_jun",
        )


@pytest.mark.parametrize(
    ("year", "round_name", "expected_horizon"),
    ((2027, "jan_jun", 4344), (2028, "jan_jun", 4368), (2029, "jul_dec", 4416)),
)
def test_half_year_metadata_uses_released_calendar_horizons(
    year: int, round_name: str, expected_horizon: int
) -> None:
    horizon = HalfYearHorizon.for_round(year, round_name)

    assert horizon.horizon_hours == expected_horizon
    assert horizon.label == f"{year}_{round_name}"
    calendar = half_year_calendar(year, round_name)
    assert horizon.calendar == calendar
    assert horizon.calendar.start_date == calendar.start_date
    assert horizon.calendar.end_date == calendar.end_date
    assert horizon.calendar.source_row_start == calendar.source_row_start


def test_rl_gym_is_an_explicit_project_package() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "rl_gym" in project["tool"]["setuptools"]["packages"]
