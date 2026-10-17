from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ercot_game import batch_evaluation
from ercot_game.batch_evaluation import (
    BatchScoringRules,
    DCOPFInfeasible,
    ProjectionDiagnostics,
    TeamScore,
    _fair_continuous_projection,
    _score_team,
)
from ercot_game.calendars import half_year_calendar
from ercot_game.csv_submission import CANONICAL_CSV_COLUMNS, LEGACY_CSV_COLUMNS, read_student_csv
from ercot_game.models import ConnectionPlan, GeneratorPlan, TeamPlan
from ercot_game.round_coordinator import (
    ClassroomRoundInput,
    RoundCoordinatorError,
    evaluate_classroom,
    evaluate_round,
)
from scripts import create_half_year_scoring_rules


REPOSITORY_ROOT = Path(__file__).parents[1]
CANONICAL_HEADER = ",".join(CANONICAL_CSV_COLUMNS)


def _write_canonical(path: Path, rows: list[list[object]], *, header: str = CANONICAL_HEADER) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header.split(","))
        writer.writerows(rows)


def test_canonical_csv_rejects_malformed_missing_extra_and_legacy_shapes(tmp_path: Path) -> None:
    cases = {
        "missing": ",".join(CANONICAL_CSV_COLUMNS[:-1]),
        "extra": CANONICAL_HEADER + ",unexpected",
        "legacy": ",".join(LEGACY_CSV_COLUMNS),
    }

    for name, header in cases.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(header + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="header must be exactly"):
            read_student_csv(path)


def test_canonical_csv_rejects_malformed_csv_and_duplicate_rows(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.csv"
    malformed.write_text(
        CANONICAL_HEADER + "\n"
        + 'team-a,site-a,3002041,"40,0,0,0\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed CSV"):
        read_student_csv(malformed)

    duplicate = tmp_path / "duplicate.csv"
    row = ["team-a", "site-a", "3002041", 40, 0, 0, 0]
    _write_canonical(duplicate, [row, row])
    with pytest.raises(ValueError, match="duplicate site_id"):
        read_student_csv(duplicate, bus_ids={"3002041"})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("connection_capacity_mw", "nan", "must be finite"),
        ("generator_capacity_mw", "-1", "must be non-negative"),
        ("storage_power_mw", "inf", "must be finite"),
        ("storage_energy_mwh", "-4", "must be non-negative"),
    ),
)
def test_canonical_csv_rejects_nonfinite_and_negative_capacities(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    row = ["team-a", "site-a", "3002041", "40", "0", "0", "0"]
    row[list(CANONICAL_CSV_COLUMNS).index(field)] = value
    path = tmp_path / f"bad-{field}.csv"
    _write_canonical(path, [row])

    with pytest.raises(ValueError, match=message):
        read_student_csv(path, bus_ids={"3002041"})


def test_canonical_csv_rejects_oversized_generator_and_wrong_bus(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized-generator.csv"
    _write_canonical(oversized, [["team-a", "site-a", "3002041", 10, 10.1, 0, 0]])
    with pytest.raises(ValueError, match="generator capacity.*connection capacity"):
        read_student_csv(oversized, bus_ids={"3002041"})

    wrong_bus = tmp_path / "wrong-bus.csv"
    _write_canonical(wrong_bus, [["team-a", "site-a", "9999999", 10, 0, 0, 0]])
    with pytest.raises(ValueError, match="unknown scenario bus"):
        read_student_csv(wrong_bus, bus_ids={"3002041"})


def test_canonical_path_and_explicit_legacy_parser_do_not_confuse_each_other(tmp_path: Path) -> None:
    canonical_path = tmp_path / "legacy-looking-name.csv"
    _write_canonical(canonical_path, [["team-a", "site-a", "3002041", 10, 0, 0, 0]])
    assert read_student_csv(canonical_path, bus_ids={"3002041"}).submission_id is None

    legacy_path = tmp_path / "canonical-looking-name.csv"
    legacy_path.write_text(
        ",".join(LEGACY_CSV_COLUMNS) + "\n"
        "team-a,week-1,1,site-a,10,,,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="header must be exactly"):
        read_student_csv(legacy_path)


def test_valid_canonical_multi_site_submission_is_preserved_as_one_team(tmp_path: Path) -> None:
    path = tmp_path / "multi-site.csv"
    _write_canonical(
        path,
        [
            ["team-a", "site-north", "3002041", 40, 20, 10, 40],
            ["team-a", "site-south", "3002042", 15, 0, 0, 0],
        ],
    )

    submission = read_student_csv(path, bus_ids={"3002041", "3002042"})
    assert submission.team_id == "team-a"
    assert submission.submission_id is None
    assert [site.site_id for site in submission.plan.sites] == ["site-north", "site-south"]
    assert [site.bus_id for site in submission.plan.sites] == ["3002041", "3002042"]


def _audit_plan(team_id: str, site_id: str, generator_id: str, capacity: float = 4.0) -> TeamPlan:
    return TeamPlan(
        team_id,
        connections=(ConnectionPlan(site_id, "b0001", capacity, (1.0,)),),
        generators=(GeneratorPlan(generator_id, site_id, 1.0, 16.0),),
    )


def _empty_plan(team_id: str) -> TeamPlan:
    return TeamPlan(team_id, connections=())


def _audit_evaluation(**kwargs):
    plans = tuple(kwargs["plan_overrides"].values())
    submission_number = kwargs["submission_number"]
    team_ids = tuple(plan.team_id for plan in plans)
    scores = tuple(
        TeamScore(
            team_id=team_id,
            rank=0,
            valid=True,
            feasible=True,
            score=10.0 if team_id == "team-a" else 20.0,
            served_load_mwh=1.0,
            it_value=10.0 if team_id == "team-a" else 20.0,
            energy_cost=0.0,
            generator_operating_cost=0.0,
            generator_capital_cost=0.0,
            site_cost=0.0,
            storage_capital_cost=0.0,
            competition_scope="joint",
        )
        for team_id in team_ids
    )
    dispatch = SimpleNamespace(
        team_ids=team_ids,
        periods=(
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={team_id: 1.0 for team_id in team_ids},
                generator_dispatch_mw_by_team={team_id: {} for team_id in team_ids},
            ),
        ),
    )
    assert submission_number in (1, 2)
    return SimpleNamespace(
        rejected_submissions=(),
        dispatch=dispatch,
        projection_diagnostics=None,
        team_scores=scores,
    )


def test_full_six_round_lifecycle_has_feedback_scored_and_reset_boundaries(tmp_path: Path) -> None:
    plans_by_round = {
        "2027_jan_jun": (_audit_plan("team-a", "site-w1", "gen-w1"), _audit_plan("team-b", "site-b1", "gen-b1")),
        "2027_jul_dec": (_empty_plan("team-a"), _empty_plan("team-b")),
        "2028_jan_jun": (_audit_plan("team-a", "site-w2", "gen-w2"), _audit_plan("team-b", "site-b2", "gen-b2")),
        "2028_jul_dec": (_empty_plan("team-a"), _empty_plan("team-b")),
        "2029_jan_jun": (_audit_plan("team-a", "site-w3", "gen-w3"), _audit_plan("team-b", "site-b3", "gen-b3")),
        "2029_jul_dec": (_empty_plan("team-a"), _empty_plan("team-b")),
    }
    def plan_loader(submissions, **_kwargs):
        return plans_by_round[str(submissions)]

    def evaluator(*_args, **kwargs):
        return _audit_evaluation(**kwargs)

    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=round_key,
            artifact_dir=tmp_path,
            horizon=1,
            rules=object(),
        )
        for round_key in plans_by_round
    )
    result = evaluate_classroom(
        state_path=tmp_path / "game-state.json",
        rounds=rounds,
        plan_loader=plan_loader,
        evaluator=evaluator,
    )

    assert [item.round_key for item in result.submissions] == list(plans_by_round)
    assert result.submissions[0].feedback_only is True
    assert result.submissions[0].scored is False
    assert result.submissions[1].feedback_only is True
    assert result.submissions[1].preserves_prior_submission is True
    assert result.submissions[2].ledger_reset is True
    assert result.submissions[2].scored is True
    assert result.submissions[3].scored is True
    assert result.submissions[3].preserves_prior_submission is True
    assert result.submissions[4].ledger_reset is True
    assert result.submissions[4].scored is True
    assert result.submissions[5].scored is True
    assert result.submissions[2].prior_state.asset_ledger.team_ids() == ()
    assert result.submissions[3].prior_state.asset_ledger.team("team-a").generators["gen-w2"].capacity_mw == 1.0
    assert result.submissions[4].prior_state.asset_ledger.team_ids() == ()
    assert result.submissions[5].prior_state.asset_ledger.team("team-a").generators["gen-w3"].capacity_mw == 1.0
    assert result.weekly_rankings[0].rankings == ()
    assert all(ranking.rankings for ranking in result.weekly_rankings[1:])
    # Final ranking sums Week 2 S1 + S2 + Week 3 S1 + S2 (Week 1 excluded).
    assert {score.team_id: score.score for score in result.final_rankings} == {
        "team-a": 40.0,
        "team-b": 80.0,
    }


def test_submission_one_has_no_energy_charge_and_submission_two_uses_joint_lmp() -> None:
    dispatch = SimpleNamespace(
        periods=(
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={"team-a": 2.0},
                generator_dispatch_mw_by_team={"team-a": {}},
                grid_purchase_mw_by_team={"team-a": 2.0},
                grid_purchase_mw_by_team_by_bus={"team-a": {"B1": 2.0}},
                dcopf=SimpleNamespace(balance_dual_by_bus={"B1": 40.0}),
            ),
        ),
    )
    plan = TeamPlan("team-a", (ConnectionPlan("site-a", "B1", 2.0, (1.0,)),))
    rules = BatchScoringRules(
        rules_version="task10-test",
        it_value_per_mwh=(1500.0,),
        energy_price_per_mwh=(999.0,),
    )

    submission_one = _score_team(plan, dispatch, rules, submission_number=1)
    submission_two = _score_team(plan, dispatch, rules, submission_number=2)
    assert submission_one.energy_cost == 0.0
    assert submission_two.energy_cost == 80.0
    assert submission_two.score == submission_one.score - submission_two.energy_cost


def test_fair_projection_handles_missing_catalog_and_zero_request_without_leakage(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _audit_plan("team-valid", "site-valid", "gen-valid", capacity=4.0)
    assert _fair_continuous_projection(tmp_path, (plan,), (0,), (0,)) is None

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text("bus_id\nb0001\n", encoding="utf-8")
    zero = _audit_plan("team-zero", "site-zero", "gen-zero", capacity=0.0)
    calls = []

    def dispatch(_artifact_dir, candidate_plans, period_indices, **_kwargs):
        capacities = {
            item.team_id: sum(connection.capacity_mw for connection in item.connections)
            for item in candidate_plans
        }
        calls.append(capacities)
        if sum(capacities.values()) > 4.0 + 1e-8:
            raise DCOPFInfeasible("synthetic projection overload")
        return SimpleNamespace(
            team_ids=tuple(capacities),
            periods=(
                SimpleNamespace(
                    period_index=period_indices[0],
                    submitted_load_mw_by_team={team_id: value for team_id, value in capacities.items()},
                    generator_dispatch_mw_by_team={team_id: {} for team_id in capacities},
                ),
            ),
        )

    monkeypatch.setattr(batch_evaluation, "_dispatch_plans", dispatch)
    projection = _fair_continuous_projection(
        artifact,
        (plan, zero),
        (0,),
        (0,),
        projection_iterations=8,
    )
    assert projection is not None
    assert set(projection.service_fraction_by_team) == {"team-valid", "team-zero"}
    assert projection.diagnostics.requested_load_mwh_by_team["team-zero"] == 0.0
    assert projection.projected_capacity_mw_by_team["team-zero"] == 0.0
    assert all("team-rejected" not in call for call in calls)


def test_active_release_surfaces_match_six_six_hour_blocks_and_generator_status() -> None:
    active_docs = (
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md",
        REPOSITORY_ROOT / "docs" / "HYPERPARAMETERS.md",
    )
    for path in active_docs:
        text = path.read_text(encoding="utf-8")
        assert CANONICAL_HEADER in text
        assert "Submission 1" in text and "Submission 2" in text
        assert "half-year" in text.lower()
        assert "six-period" not in text.lower()

    root = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v2_6h"
    block_manifests = sorted(root.glob("20??/*/manifest.json"))
    assert len(block_manifests) == 6
    assert [json.loads(path.read_text(encoding="utf-8"))["horizon"] for path in block_manifests] == [
        half_year_calendar(int(path.parts[-3]), path.parts[-2]).horizon // 6
        for path in block_manifests
    ]

    for rules_name, round_name in (
        ("dispatch_scoring_rules_jan_jun_6h_v1.json", "jan_jun"),
        ("dispatch_scoring_rules_jul_dec_6h_v1.json", "jul_dec"),
    ):
        rules = json.loads((REPOSITORY_ROOT / "data" / "teaching" / rules_name).read_text(encoding="utf-8"))
        generated = create_half_year_scoring_rules.build_rules_for_year_season(
            year=2028,
            round_name=round_name,
            time_step_hours=6,
        )
        assert rules == generated
        assert rules["status"] == "released"
        assert len(rules["it_value_per_mwh"]) == half_year_calendar(2028, round_name).horizon // 6


def test_projection_diagnostics_reject_unknown_team_at_round_boundary(tmp_path: Path) -> None:
    diagnostics = ProjectionDiagnostics(
        service_fraction_by_team={"team-valid": 0.5, "team-rejected": 0.5},
        projected_capacity_mw_by_team={"team-valid": 1.0, "team-rejected": 1.0},
        capped_request_capacity_mw_by_team={"team-valid": 2.0, "team-rejected": 2.0},
        requested_load_mwh_by_team={"team-valid": 2.0, "team-rejected": 2.0},
        projected_load_mwh_by_team={"team-valid": 1.0, "team-rejected": 1.0},
        total_requested_load_mwh=4.0,
        total_projected_load_mwh=2.0,
        proportional_fair_utility=0.0,
        total_load_tolerance_mwh=1e-6,
    )
    def evaluator(*args, **kwargs):
        result = _audit_evaluation(**kwargs)
        result.projection_diagnostics = diagnostics
        return result

    with pytest.raises(RoundCoordinatorError, match="rejected or unknown team"):
        evaluate_round(
            state_path=tmp_path / "state.json",
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifact",
            horizon=1,
            rules=object(),
            plan_loader=lambda *args, **kwargs: (_audit_plan("team-valid", "site-valid", "gen-valid"),),
            evaluator=evaluator,
        )
