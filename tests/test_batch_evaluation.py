from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from ercot_game import batch_evaluation
from ercot_game.calendars import half_year_calendar
from ercot_game.batch_evaluation import (
    BatchCollection,
    BatchScoringRules,
    CapitalCostScope,
    SubmissionRecord,
    _score_team,
    collect_csv_submissions,
    evaluate_csv_batch,
    write_batch_outputs,
    load_scoring_rules,
)
from ercot_game.csv_submission import CSVSubmission, LEGACY_CSV_COLUMNS, read_student_csv
from ercot_game.batch_evaluation import DCOPFInfeasible
from ercot_game.economic_profiles import load_economic_profiles
from ercot_game.models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan


def _write_submission(
    path,
    team_id: str,
    *,
    submission_id: str = "week-1",
    site_id: str = "PANHANDLE",
    storage_id: str = "",
    storage_power_mw: str = "",
    storage_energy_mwh: str = "",
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "team_id": team_id,
                "submission_id": submission_id,
                "period": "1",
                "site_id": site_id,
                "load_mw": "2",
                "generator_id": "",
                "generator_capacity_mw": "",
                "storage_id": storage_id,
                "storage_power_mw": storage_power_mw,
                "storage_energy_mwh": storage_energy_mwh,
            }
        )


def _collect(directory):
    return collect_csv_submissions(
        directory,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        default_generator_cost_per_mwh=100.0,
    )


def _write_artifact_catalogs(root: Path) -> Path:
    artifact = root / "artifact"
    artifact.mkdir(exist_ok=True)
    (artifact / "bus.csv").write_text("bus_id\n3002041\n", encoding="utf-8")
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,42.5\n",
        encoding="utf-8",
    )
    return artifact


def test_discovery_is_filename_deterministic_and_acceptance_is_team_sorted(tmp_path):
    first_filename = tmp_path / "z-submission.csv"
    second_filename = tmp_path / "a-submission.csv"
    _write_submission(first_filename, "team-a")
    _write_submission(second_filename, "team-z")
    (tmp_path / "ignored.txt").write_text("not a CSV", encoding="utf-8")

    collection = _collect(tmp_path)

    assert [record.path for record in collection.accepted] == [first_filename, second_filename]
    assert [record.team_id for record in collection.accepted] == ["team-a", "team-z"]
    assert collection.rejected == ()


def test_discovery_reads_csv_files_in_filename_order(tmp_path, monkeypatch):
    _write_submission(tmp_path / "b.csv", "team-a")
    _write_submission(tmp_path / "a.csv", "team-z")
    identity_paths = []
    parser_paths = []
    original_identity = batch_evaluation.read_submission_identity
    original_parser = batch_evaluation.read_student_csv

    def recording_identity(path):
        identity_paths.append(Path(path).name)
        return original_identity(path)

    def recording_parser(path, **kwargs):
        parser_paths.append(Path(path).name)
        return original_parser(path, **kwargs)

    monkeypatch.setattr(batch_evaluation, "read_submission_identity", recording_identity)
    monkeypatch.setattr(batch_evaluation, "read_student_csv", recording_parser)

    collection = _collect(tmp_path)

    assert identity_paths == ["a.csv", "b.csv"]
    assert parser_paths == ["a.csv", "b.csv"]
    assert [record.team_id for record in collection.accepted] == ["team-a", "team-z"]


def test_explicit_paths_are_read_in_deterministic_full_path_order(tmp_path, monkeypatch):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    left_path = left / "same.csv"
    right_path = right / "same.csv"
    _write_submission(left_path, "team-left")
    _write_submission(right_path, "team-right")
    observed_paths = []
    original_identity = batch_evaluation.read_submission_identity

    def recording_identity(path):
        observed_paths.append(Path(path))
        return original_identity(path)

    monkeypatch.setattr(batch_evaluation, "read_submission_identity", recording_identity)
    collection = batch_evaluation._collect_evaluation_inputs(
        (right_path, left_path),
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        default_generator_cost_per_mwh=100.0,
    )

    assert observed_paths == sorted((left_path, right_path), key=lambda path: str(path.resolve()))
    assert [record.path for record in collection.accepted] == [left_path, right_path]


def test_malformed_file_is_rejected_without_blocking_valid_discovery(tmp_path):
    valid_path = tmp_path / "valid.csv"
    malformed_path = tmp_path / "malformed.csv"
    _write_submission(valid_path, "team-valid")
    malformed_path.write_bytes(b"not,a,valid,submission\n\xff\n")

    collection = _collect(tmp_path)

    assert [record.team_id for record in collection.accepted] == ["team-valid"]
    assert len(collection.rejected) == 1
    rejected = collection.rejected[0]
    assert rejected.valid is False
    assert rejected.failure_reason
    assert rejected.path == malformed_path
    assert rejected.team_id == "UNPARSED::malformed.csv"


def test_oversized_csv_is_rejected_without_blocking_valid_discovery(tmp_path):
    valid_path = tmp_path / "valid.csv"
    oversized_path = tmp_path / "oversized.csv"
    _write_submission(valid_path, "team-valid")
    oversized_path.write_text(
        ",".join(LEGACY_CSV_COLUMNS) + "\n"
        + ("team-bad,week-1,1,PANHANDLE," + ("9" * 131073) + ",,,,,\n"),
        encoding="utf-8",
    )

    collection = _collect(tmp_path)

    assert [record.team_id for record in collection.accepted] == ["team-valid"]
    assert len(collection.rejected) == 1
    assert "malformed CSV" in (collection.rejected[0].failure_reason or "")


def test_duplicate_declared_team_ids_are_all_retained_as_rejected(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    unique_path = tmp_path / "unique.csv"
    _write_submission(first_path, "team-duplicate", submission_id="first")
    _write_submission(second_path, "team-duplicate", submission_id="second")
    _write_submission(unique_path, "team-unique")

    collection = _collect(tmp_path)

    assert [record.team_id for record in collection.accepted] == ["team-unique"]
    assert [record.path for record in collection.rejected] == [first_path, second_path]
    assert all(record.valid is False for record in collection.rejected)
    assert all("duplicate team_id" in (record.failure_reason or "") for record in collection.rejected)


def test_discovery_rejects_valid_and_malformed_files_with_same_declared_team_id(tmp_path):
    valid_path = tmp_path / "valid.csv"
    malformed_path = tmp_path / "malformed.csv"
    _write_submission(valid_path, "team-a")
    _write_submission(malformed_path, "team-a", site_id="UNKNOWN")

    collection = _collect(tmp_path)

    assert collection.accepted == ()
    assert [record.path for record in collection.rejected] == [malformed_path, valid_path]
    assert all(record.team_id == "team-a" for record in collection.rejected)
    assert all("duplicate team_id" in (record.failure_reason or "") for record in collection.rejected)


def test_fallback_and_declared_ids_collide_even_when_declared_id_has_unparsed_prefix(tmp_path):
    fallback_path = tmp_path / "team-a.csv"
    declared_path = tmp_path / "declared.csv"
    fallback_path.write_text("not a CSV", encoding="utf-8")
    _write_submission(declared_path, "UNPARSED::team-a.csv")

    collection = _collect(tmp_path)

    assert collection.accepted == ()
    records_by_path = {record.path: record for record in collection.rejected}
    assert records_by_path[fallback_path].identity_is_fallback is True
    assert records_by_path[declared_path].identity_is_fallback is False
    assert all("duplicate team_id" in (record.failure_reason or "") for record in collection.rejected)


@pytest.mark.parametrize(
    ("storage_field", "storage_value"),
    (
        ("storage_id", "battery-1"),
        ("storage_power_mw", "10"),
        ("storage_energy_mwh", "20"),
    ),
)
def test_storage_fields_remain_reserved_and_csv_columns_are_unchanged(
    tmp_path, storage_field, storage_value
):
    path = tmp_path / f"storage-{storage_field}.csv"
    _write_submission(path, "team-storage", **{storage_field: storage_value})

    with pytest.raises(ValueError, match="storage is reserved"):
        read_student_csv(
            path,
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            allow_legacy=True,
        )

    assert LEGACY_CSV_COLUMNS == (
        "team_id",
        "submission_id",
        "period",
        "site_id",
        "load_mw",
        "generator_id",
        "generator_capacity_mw",
        "storage_id",
        "storage_power_mw",
        "storage_energy_mwh",
    )
    collection = _collect(tmp_path)
    assert collection.accepted == ()
    assert collection.rejected[0].team_id == "team-storage"


def test_batch_collection_and_submission_record_are_frozen():
    record = SubmissionRecord(
        path=Path("submission.csv"),
        team_id="team-a",
        submission_id=None,
        submission=None,
        valid=False,
        failure_reason="invalid",
    )
    collection = BatchCollection(accepted=(), rejected=(record,))

    with pytest.raises(FrozenInstanceError):
        record.valid = True
    with pytest.raises(FrozenInstanceError):
        collection.accepted = ()


def test_discovery_rejects_missing_or_non_directory_input(tmp_path):
    missing = tmp_path / "missing"
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        _collect(missing)
    with pytest.raises(ValueError):
        _collect(file_path)


def _rules() -> BatchScoringRules:
    return BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=(200.0,),
        energy_price_per_mwh=(50.0,),
        infeasible_score=-999.0,
    )


def _fake_dispatch(
    team_ids: tuple[str, ...],
    *,
    unknown_generator_amount: float = 0.0,
    load_by_team: dict[str, float] | None = None,
    period_indices: tuple[int, ...] = (0,),
):
    loads = load_by_team or {team_id: 2.0 for team_id in team_ids}
    return SimpleNamespace(
        team_ids=team_ids,
        periods=tuple(
            SimpleNamespace(
                period_index=period_index,
                dcopf=SimpleNamespace(
                    status="optimal",
                    objective_cost=0.0,
                    generation_mw={"grid": 4.0},
                    flow_mw_by_branch={"branch-1": 2.0},
                    balance_dual_by_bus={"bus-1": 50.0},
                ),
                submitted_load_mw_by_team=loads,
                generator_dispatch_mw_by_team={
                    team_id: {"gen-1": unknown_generator_amount} for team_id in team_ids
                },
            )
            for period_index in period_indices
        ),
    )


def _write_outputs(result, output_dir, *, period_indices=(0,)):
    write_batch_outputs(
        result,
        output_dir,
        scenario_version="scenario-v1",
        evaluator_version="evaluator-v1",
        period_indices=period_indices,
    )


def _evaluate_paths(
    tmp_path,
    monkeypatch,
    paths,
    dispatch,
    *,
    default_generator_cost_per_mwh=None,
):
    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    artifact_dir = _write_artifact_catalogs(tmp_path)
    return evaluate_csv_batch(
        paths,
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
        default_generator_cost_per_mwh=default_generator_cost_per_mwh,
    )


def test_evaluate_csv_batch_scores_feasible_joint_set(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)

    assert result.competition_scope == "joint"
    assert result.rejected_submissions == ()
    assert [score.team_id for score in result.team_scores] == ["team-a", "team-b"]
    assert all(score.valid and score.feasible for score in result.team_scores)
    assert all(score.competition_scope == "joint" for score in result.team_scores)


def test_canonical_site_plan_is_expanded_before_batch_dispatch(tmp_path, monkeypatch):
    path = tmp_path / "team-a.csv"
    path.write_text(
        "team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,"
        "storage_power_mw,storage_energy_mwh\n"
        "team-a,site-a,b0001,40,10,5,20\n"
        "team-a,site-b,b0002,30,0,0,0\n",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def dispatch(_artifact_dir, plans, *, period_indices):
        seen["plans"] = tuple(plans)
        return _fake_dispatch(("team-a",), period_indices=tuple(period_indices))

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        path,
        artifact_dir=artifact_dir,
        bus_ids={"b0001", "b0002"},
        horizon=3,
        rules=BatchScoringRules(
            rules_version="test-v1",
            it_value_per_mwh=(200.0, 200.0, 200.0),
            energy_price_per_mwh=(50.0, 50.0, 50.0),
        ),
        source_period_indices=(4, 5, 6),
    )

    plan = seen["plans"][0]
    assert [connection.profile for connection in plan.connections] == [
        (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
        (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
    ]
    assert {generator.connection_id for generator in plan.generators} == {"site-a"}
    assert result.team_scores[0].served_load_mwh == 6.0


def test_evaluate_csv_batch_uses_materialized_plan_and_charges_only_new_assets(
    tmp_path, monkeypatch
):
    path = tmp_path / "team-a.csv"
    parsed_plan = TeamPlan(
        "team-a",
        (ConnectionPlan("site-current", "b0001", 10.0, (1.0,)),),
    )
    materialized_plan = TeamPlan(
        "team-a",
        (
            ConnectionPlan("site-current", "b0001", 10.0, (1.0,)),
            ConnectionPlan("site-old", "b0001", 0.0, (0.0,)),
        ),
        generators=(
            GeneratorPlan("gen-new", "site-current", 5.0, 16.0),
            GeneratorPlan("gen-old", "site-old", 10.0, 16.0),
        ),
        storages=(
            StoragePlan("store-new", "site-current", 2.0, 10.0),
            StoragePlan("store-old", "site-old", 3.0, 20.0),
        ),
    )
    record = SubmissionRecord(
        path=path,
        team_id="team-a",
        submission_id="round-2",
        submission=CSVSubmission("team-a", "round-2", parsed_plan, "parsed-sha"),
        valid=True,
        failure_reason=None,
        input_sha256="input-sha",
    )
    monkeypatch.setattr(
        batch_evaluation,
        "_collect_evaluation_inputs",
        lambda *args, **kwargs: BatchCollection((record,), ()),
    )
    seen: dict[str, object] = {}

    def dispatch(_artifact_dir, plans, period_indices, **kwargs):
        seen["plans"] = tuple(plans)
        return SimpleNamespace(
            team_ids=("team-a",),
            periods=(
                SimpleNamespace(
                    period_index=0,
                    submitted_load_mw_by_team={"team-a": 10.0},
                    generator_dispatch_mw_by_team={
                        "team-a": {"gen-old": 1.0, "gen-new": 2.0}
                    },
                    grid_purchase_mw_by_team={"team-a": 7.0},
                    storage_discharge_mw_by_team={
                        "team-a": {"store-old": 1.0, "store-new": 1.0}
                    },
                ),
            ),
        )

    monkeypatch.setattr(batch_evaluation, "_dispatch_plans", dispatch)
    rules = BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=(200.0,),
        energy_price_per_mwh=(50.0,),
        generator_capital_cost_per_mw=10.0,
        site_cost_per_site=7.0,
        storage_capital_cost_per_mw=3.0,
        storage_capital_cost_per_mwh=4.0,
    )

    result = evaluate_csv_batch(
        path,
        artifact_dir=tmp_path,
        bus_ids={"b0001"},
        horizon=1,
        rules=rules,
        plan_overrides={"team-a": materialized_plan},
        capital_cost_scope=CapitalCostScope(
            generator_ids_by_team={"team-a": {"gen-new"}},
            storage_ids_by_team={"team-a": {"store-new"}},
        ),
        site_cost_connection_count_by_team={"team-a": 1},
    )

    score = result.team_scores[0]
    assert seen["plans"] == (materialized_plan,)
    assert result.input_sha256_by_team == {"team-a": "parsed-sha"}
    assert score.generator_capital_cost == 50.0
    assert score.storage_capital_cost == 46.0
    assert score.generator_operating_cost == 210.0
    assert score.site_cost == 7.0


def test_direct_batch_evaluation_keeps_default_materialized_connection_site_cost(
    tmp_path, monkeypatch
):
    path = tmp_path / "team-a.csv"
    plan = TeamPlan(
        "team-a",
        (
            ConnectionPlan("site-1", "b0001", 1.0, (1.0,)),
            ConnectionPlan("site-2", "b0001", 0.0, (0.0,)),
        ),
    )
    record = SubmissionRecord(
        path=path,
        team_id="team-a",
        submission_id="round-1",
        submission=CSVSubmission("team-a", "round-1", plan, "sha"),
        valid=True,
        failure_reason=None,
        input_sha256="sha",
    )
    monkeypatch.setattr(
        batch_evaluation,
        "_collect_evaluation_inputs",
        lambda *args, **kwargs: BatchCollection((record,), ()),
    )
    monkeypatch.setattr(
        batch_evaluation,
        "_dispatch_plans",
        lambda *args, **kwargs: SimpleNamespace(
            team_ids=("team-a",),
            periods=(
                SimpleNamespace(
                    period_index=0,
                    submitted_load_mw_by_team={"team-a": 1.0},
                    generator_dispatch_mw_by_team={"team-a": {}},
                ),
            ),
        ),
    )

    result = evaluate_csv_batch(
        path,
        artifact_dir=tmp_path,
        bus_ids={"b0001"},
        horizon=1,
        rules=BatchScoringRules(
            rules_version="test-v1",
            it_value_per_mwh=(1.0,),
            energy_price_per_mwh=(0.0,),
            site_cost_per_site=7.0,
        ),
    )

    assert result.team_scores[0].site_cost == 14.0


def test_evaluate_csv_batch_rejects_unknown_capital_scope_asset(tmp_path, monkeypatch):
    path = tmp_path / "team-a.csv"
    plan = TeamPlan("team-a", (ConnectionPlan("site", "b0001", 1.0, (1.0,)),))
    record = SubmissionRecord(
        path=path,
        team_id="team-a",
        submission_id="round-1",
        submission=CSVSubmission("team-a", "round-1", plan, "sha"),
        valid=True,
        failure_reason=None,
        input_sha256="sha",
    )
    monkeypatch.setattr(
        batch_evaluation,
        "_collect_evaluation_inputs",
        lambda *args, **kwargs: BatchCollection((record,), ()),
    )

    with pytest.raises(ValueError, match="unknown generator asset"):
        evaluate_csv_batch(
            path,
            artifact_dir=tmp_path,
            bus_ids={"b0001"},
            horizon=1,
            rules=_rules(),
            capital_cost_scope=CapitalCostScope(
                generator_ids_by_team={"team-a": {"not-in-plan"}}
            ),
        )


def test_evaluate_csv_batch_isolates_first_leave_one_out_team(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        if team_ids == ("team-a", "team-b"):
            raise DCOPFInfeasible("synthetic joint overload")
        return _fake_dispatch(team_ids)

    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)
    scores = {score.team_id: score for score in result.team_scores}

    assert result.competition_scope == "joint"
    assert scores["team-a"].score == -999.0
    assert scores["team-a"].feasible is False
    assert scores["team-a"].competition_scope == "not_evaluated"
    assert scores["team-b"].feasible is True
    assert scores["team-b"].competition_scope == "joint"
    recovery = next(
        event for event in result.isolation_events if event.outcome == "feasible_remainder"
    )
    assert recovery.removed_team_id == "team-a"
    assert "legacy order-dependent team-removal fallback" in recovery.reason


def test_evaluate_csv_batch_uses_legacy_subset_then_individual_fallback(
    tmp_path, monkeypatch
):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    calls = []

    # Projection is explicitly unavailable in this test. The joint attempt
    # and two legacy subset attempts fail before individual fallback.
    failures_before_individual = 3

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        calls.append(team_ids)
        if len(calls) <= failures_before_individual:
            raise DCOPFInfeasible("synthetic shared overload")
        return _fake_dispatch(team_ids)

    monkeypatch.setattr(
        batch_evaluation,
        "_fair_continuous_projection",
        lambda *args, **kwargs: None,
    )
    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)

    assert result.competition_scope == "individual_fallback"
    assert all(score.valid and score.feasible for score in result.team_scores)
    assert all(score.competition_scope == "individual_fallback" for score in result.team_scores)
    assert result.dispatch is None
    assert result.isolation_events[-1].outcome == "individual_fallback"
    assert "fair continuous projection unavailable" in result.isolation_events[-1].reason


def test_fair_continuous_projection_finds_fraction_below_old_bisection_resolution(
    tmp_path, monkeypatch
):
    submission = tmp_path / "team-a.csv"
    _write_submission(submission, "team-a")
    artifact_dir = _write_artifact_catalogs(tmp_path)
    plan = read_student_csv(
        submission,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        allow_legacy=True,
    ).plan
    observed_alphas = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        alpha = plans[0].connections[0].capacity_mw / 2.0
        observed_alphas.append(alpha)
        if alpha > 0.0001:
            raise DCOPFInfeasible("synthetic overload below old resolution")
        return _fake_dispatch(("team-a",), period_indices=period_indices)

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    projection = batch_evaluation._fair_continuous_projection(
        artifact_dir,
        (plan,),
        (0,),
        (0,),
        projection_iterations=10,
    )

    assert projection is not None
    _dispatch, service_fraction, _projected_plans = projection
    assert 0.0 < service_fraction < 1 / 1024
    assert service_fraction == pytest.approx(0.0001, abs=2e-6)
    assert observed_alphas[0] == 0.0


def test_zero_service_projection_prevents_order_dependent_team_removal(
    tmp_path, monkeypatch
):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")
    calls = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        total_capacity = sum(
            connection.capacity_mw
            for plan in plans
            for connection in plan.connections
        )
        calls.append((team_ids, total_capacity))
        if len(plans) > 1 and total_capacity > 0.0:
            raise DCOPFInfeasible("synthetic overload at every positive fraction")
        return _fake_dispatch(team_ids, period_indices=period_indices)

    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)

    assert result.competition_scope == "joint_projection"
    assert all(score.feasible for score in result.team_scores)
    assert all(score.competition_scope == "joint_projection" for score in result.team_scores)
    assert any(total_capacity == 0.0 for _team_ids, total_capacity in calls)
    projection_event = next(
        event for event in result.isolation_events if event.outcome == "joint_projected"
    )
    assert "service fraction=0" in projection_event.reason
    assert "minimum positive service fraction=1e-06" in projection_event.reason
    assert "bisection tolerance=1e-06" in projection_event.reason
    assert not any(
        event.outcome in {"feasible_remainder", "individual_fallback"}
        for event in result.isolation_events
    )


def _projection_plan(team_id: str, capacity_mw: float) -> TeamPlan:
    return TeamPlan(
        team_id,
        (ConnectionPlan(f"site-{team_id}", "b0001", capacity_mw, (1.0,)),),
    )


def _run_projection_with_capacity_oracle(
    monkeypatch,
    tmp_path,
    plans,
    feasible,
):
    artifact_dir = _write_artifact_catalogs(tmp_path)
    calls = []

    def dispatch(_artifact_dir, candidate_plans, period_indices, **_kwargs):
        capacities = {
            plan.team_id: sum(connection.capacity_mw for connection in plan.connections)
            for plan in candidate_plans
        }
        calls.append(capacities)
        if not feasible(capacities):
            raise DCOPFInfeasible("synthetic projected overload")
        return _fake_dispatch(
            tuple(sorted(capacities)),
            load_by_team=capacities,
            period_indices=tuple(period_indices),
        )

    monkeypatch.setattr(batch_evaluation, "_dispatch_plans", dispatch)
    projection = batch_evaluation._fair_continuous_projection(
        artifact_dir,
        tuple(plans),
        (0,),
        (0,),
        projection_iterations=12,
    )
    return projection, calls


def test_fair_projection_computes_heterogeneous_team_fractions(tmp_path, monkeypatch):
    projection, _calls = _run_projection_with_capacity_oracle(
        monkeypatch,
        tmp_path,
        (_projection_plan("team-a", 4.0), _projection_plan("team-b", 4.0)),
        lambda capacities: capacities["team-a"] + 2.0 * capacities["team-b"] <= 8.0 + 1e-8,
    )

    assert projection is not None
    assert projection.service_fraction_by_team["team-a"] == pytest.approx(1.0, abs=2e-5)
    assert projection.service_fraction_by_team["team-b"] == pytest.approx(0.5, abs=2e-5)
    assert projection.projected_capacity_mw_by_team == pytest.approx(
        {"team-a": 4.0, "team-b": 2.0}, abs=2e-4
    )


def test_fair_projection_maximizes_total_load_before_fair_tie_break(tmp_path, monkeypatch):
    projection, _calls = _run_projection_with_capacity_oracle(
        monkeypatch,
        tmp_path,
        (_projection_plan("team-a", 4.0), _projection_plan("team-b", 4.0)),
        lambda capacities: sum(capacities.values()) <= 5.0 + 1e-8,
    )

    assert projection is not None
    assert projection.total_projected_load_mwh == pytest.approx(5.0, abs=2e-4)
    assert projection.projected_capacity_mw_by_team["team-b"] == pytest.approx(2.5, abs=2e-4)
    assert projection.projected_capacity_mw_by_team["team-a"] == pytest.approx(2.5, abs=2e-4)
    assert projection.service_fraction_by_team["team-a"] == pytest.approx(0.625, abs=3e-5)
    assert projection.service_fraction_by_team["team-b"] == pytest.approx(0.625, abs=3e-5)


def test_fair_projection_uses_proportional_fair_tie_break(tmp_path, monkeypatch):
    projection, _calls = _run_projection_with_capacity_oracle(
        monkeypatch,
        tmp_path,
        (_projection_plan("team-a", 4.0), _projection_plan("team-b", 4.0)),
        lambda capacities: sum(capacities.values()) <= 5.0 + 1e-8,
    )

    assert projection is not None
    assert projection.total_projected_load_mwh == pytest.approx(5.0, abs=2e-4)
    assert projection.projected_capacity_mw_by_team == pytest.approx(
        {"team-a": 2.5, "team-b": 2.5}, abs=3e-4
    )
    assert projection.proportional_fair_utility == pytest.approx(2 * math.log(2.5), abs=2e-3)


def test_fair_projection_caps_oversized_requests_before_scaling(tmp_path, monkeypatch):
    projection, _calls = _run_projection_with_capacity_oracle(
        monkeypatch,
        tmp_path,
        (_projection_plan("team-adversarial", 10_000.0), _projection_plan("team-small", 10.0)),
        lambda capacities: sum(capacities.values()) <= 60.0 + 1e-8,
    )

    assert projection is not None
    capped = projection.capped_request_capacity_mw_by_team
    assert capped["team-adversarial"] < 10_000.0
    assert capped["team-adversarial"] <= batch_evaluation._FAIR_PROJECTION_MAX_TEAM_CAPACITY_MW
    assert projection.projected_capacity_mw_by_team["team-adversarial"] <= capped["team-adversarial"]
    assert all(0.0 <= alpha <= 1.0 for alpha in projection.service_fraction_by_team.values())


def test_evaluation_records_projection_diagnostics_and_preserves_valid_team_isolation(
    tmp_path, monkeypatch
):
    valid = tmp_path / "valid.csv"
    malformed = tmp_path / "malformed.csv"
    _write_submission(valid, "team-valid")
    malformed.write_text("not,a,valid,submission\n", encoding="utf-8")

    def dispatch(_artifact_dir, plans, *, period_indices, **_kwargs):
        if sum(connection.capacity_mw for plan in plans for connection in plan.connections) > 0.0:
            raise DCOPFInfeasible("synthetic overload")
        return _fake_dispatch(
            tuple(sorted(plan.team_id for plan in plans)),
            load_by_team={plan.team_id: 0.0 for plan in plans},
            period_indices=tuple(period_indices),
        )

    result = _evaluate_paths(tmp_path, monkeypatch, (valid, malformed), dispatch)
    assert result.projection_diagnostics is not None
    assert set(result.projection_diagnostics.service_fraction_by_team) == {"team-valid"}
    assert set(result.projection_diagnostics.projected_capacity_mw_by_team) == {"team-valid"}
    assert result.team_scores[0].team_id == "team-valid"
    assert result.team_scores[0].valid is True
    assert result.team_scores[1].valid is False

    output_dir = tmp_path / "outputs"
    _write_outputs(result, output_dir)
    payload = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert payload["projection_diagnostics"]["service_fraction_by_team"] == {"team-valid": 0.0}


def test_evaluate_csv_batch_keeps_malformed_submission_out_of_solver(tmp_path, monkeypatch):
    valid = tmp_path / "valid.csv"
    malformed = tmp_path / "malformed.csv"
    _write_submission(valid, "team-valid")
    malformed.write_text("not,a,valid,submission\n", encoding="utf-8")
    calls = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        calls.append(team_ids)
        return _fake_dispatch(team_ids)

    result = _evaluate_paths(tmp_path, monkeypatch, (valid, malformed), dispatch)
    scores = {score.team_id: score for score in result.team_scores}

    assert calls == [("team-valid",)]
    assert len(result.rejected_submissions) == 1
    rejected = result.rejected_submissions[0]
    assert rejected.team_id == "UNPARSED::malformed.csv"
    assert scores["team-valid"].feasible is True
    assert scores["UNPARSED::malformed.csv"].valid is False
    assert scores["UNPARSED::malformed.csv"].feasible is False
    assert scores["UNPARSED::malformed.csv"].score == -999.0
    assert scores["UNPARSED::malformed.csv"].competition_scope == "not_evaluated"
    assert scores["UNPARSED::malformed.csv"].failure_reason


def test_batch_ranks_valid_feasible_before_valid_infeasible_before_rejected(tmp_path, monkeypatch):
    infeasible = tmp_path / "team-a-infeasible.csv"
    feasible = tmp_path / "team-b-feasible.csv"
    rejected = tmp_path / "rejected.csv"
    _write_submission(infeasible, "team-a-infeasible")
    _write_submission(feasible, "team-b-feasible")
    rejected.write_text("not,a,valid,submission\n", encoding="utf-8")

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        if team_ids == ("team-a-infeasible", "team-b-feasible"):
            raise DCOPFInfeasible("synthetic joint overload")
        return _fake_dispatch(team_ids)

    result = _evaluate_paths(tmp_path, monkeypatch, (infeasible, feasible, rejected), dispatch)

    assert [score.team_id for score in result.team_scores] == [
        "team-b-feasible",
        "team-a-infeasible",
        "UNPARSED::rejected.csv",
    ]
    assert [score.rank for score in result.team_scores] == [1, 2, 3]
    assert result.team_scores[0].valid is True
    assert result.team_scores[0].feasible is True
    assert result.team_scores[1].valid is True
    assert result.team_scores[1].feasible is False
    assert result.team_scores[2].valid is False
    assert result.team_scores[2].feasible is False


def test_score_team_rejects_positive_unknown_generator_dispatch(tmp_path):
    path = tmp_path / "team-a.csv"
    _write_submission(path, "team-a")
    submission = read_student_csv(
        path,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        allow_legacy=True,
    )

    with pytest.raises(ValueError, match="unknown generator dispatch"):
        _score_team(
            submission.plan,
            _fake_dispatch(("team-a",), unknown_generator_amount=1.0),
            _rules(),
        )


def test_score_team_rejects_duplicate_dispatch_period_indices(tmp_path):
    path = tmp_path / "team-a.csv"
    _write_submission(path, "team-a")
    submission = read_student_csv(
        path,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        allow_legacy=True,
    )
    dispatch = SimpleNamespace(
        team_ids=("team-a",),
        periods=(
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={"team-a": 2.0},
                generator_dispatch_mw_by_team={"team-a": {}},
            ),
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={"team-a": 2.0},
                generator_dispatch_mw_by_team={"team-a": {}},
            ),
        ),
    )

    with pytest.raises(ValueError, match="duplicate period index"):
        _score_team(submission.plan, dispatch, _rules())


def test_batch_ranks_by_descending_score_then_team_id(tmp_path, monkeypatch):
    paths = []
    for team_id in ("team-c", "team-a", "team-b"):
        path = tmp_path / f"{team_id}.csv"
        _write_submission(path, team_id)
        paths.append(path)

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        return _fake_dispatch(
            team_ids,
            load_by_team={"team-a": 3.0, "team-b": 2.0, "team-c": 2.0},
        )

    result = _evaluate_paths(tmp_path, monkeypatch, paths, dispatch)

    assert [score.team_id for score in result.team_scores] == ["team-a", "team-b", "team-c"]
    assert [score.rank for score in result.team_scores] == [1, 2, 3]
    assert result.team_scores[0].score > result.team_scores[1].score
    assert result.team_scores[1].score == result.team_scores[2].score


def test_batch_uses_first_successful_legacy_leave_one_out_removal_across_three_teams(
    tmp_path, monkeypatch
):
    paths = []
    for team_id in ("team-a", "team-b", "team-c"):
        path = tmp_path / f"{team_id}.csv"
        _write_submission(path, team_id)
        paths.append(path)
    calls = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        calls.append(team_ids)
        if team_ids in (("team-a", "team-b", "team-c"), ("team-b", "team-c")):
            raise DCOPFInfeasible("synthetic three-team overload")
        return _fake_dispatch(team_ids)

    monkeypatch.setattr(
        batch_evaluation,
        "_fair_continuous_projection",
        lambda *args, **kwargs: None,
    )
    result = _evaluate_paths(tmp_path, monkeypatch, paths, dispatch)
    scores = {score.team_id: score for score in result.team_scores}
    recovery = next(
        event for event in result.isolation_events if event.outcome == "feasible_remainder"
    )

    assert calls == [
        ("team-a", "team-b", "team-c"),
        ("team-b", "team-c"),
        ("team-a", "team-c"),
    ]
    assert recovery.removed_team_id == "team-b"
    assert "legacy order-dependent team-removal fallback" in recovery.reason
    assert scores["team-b"].feasible is False
    assert scores["team-a"].feasible is True
    assert scores["team-c"].feasible is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("it_value_per_mwh", (math.nan,), "finite"),
        ("energy_price_per_mwh", (math.inf,), "finite"),
        ("it_value_per_mwh", (-1.0,), "non-negative"),
        ("energy_price_per_mwh", (-1.0,), "non-negative"),
        ("time_step_hours", math.nan, "finite"),
        ("time_step_hours", 0.0, "positive"),
        ("generator_capital_cost_per_mw", math.inf, "finite"),
        ("generator_capital_cost_per_mw", -1.0, "non-negative"),
        ("site_cost_per_site", -1.0, "non-negative"),
        ("infeasible_score", math.inf, "finite"),
        ("default_generator_cost_per_mwh", -1.0, "non-negative"),
    ),
)
def test_batch_scoring_rules_reject_invalid_numeric_values(field, value, message):
    values = {
        "rules_version": "test-v1",
        "it_value_per_mwh": (200.0,),
        "energy_price_per_mwh": (50.0,),
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        BatchScoringRules(**values)


def test_batch_scoring_rules_normalizes_vectors_to_tuples():
    rules = BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=[200.0],
        energy_price_per_mwh=(50.0,),
    )

    assert rules.it_value_per_mwh == (200.0,)
    assert isinstance(rules.it_value_per_mwh, tuple)
    assert isinstance(rules.energy_price_per_mwh, tuple)


def test_scoring_settles_grid_purchase_at_each_site_bus():
    profiles = load_economic_profiles(
        {
            "horizon_hours": 1,
            "energy_price_per_mwh_by_bus": {"B1": [10.0], "B2": [20.0]},
            "it_value_per_mwh": [100.0],
            "currency_year": "2026",
            "price_source": "test baseline",
            "profile_version": "test-v1",
        }
    )
    rules = BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=(),
        energy_price_per_mwh=(),
        economic_profiles=profiles,
    )
    dispatch = SimpleNamespace(
        periods=(
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={"team-a": 2.0, "team-b": 2.0},
                generator_dispatch_mw_by_team={"team-a": {}, "team-b": {}},
                grid_purchase_mw_by_team={"team-a": 2.0, "team-b": 2.0},
                grid_purchase_mw_by_team_by_bus={
                    "team-a": {"B1": 2.0},
                    "team-b": {"B2": 2.0},
                },
                dcopf=SimpleNamespace(balance_dual_by_bus={"B1": 13.0, "B2": 17.0}),
                storage_discharge_mw_by_team={},
            ),
        ),
    )
    plan_a = TeamPlan("team-a", (ConnectionPlan("site-a", "B1", 2.0, (1.0,)),))
    plan_b = TeamPlan("team-b", (ConnectionPlan("site-b", "B2", 2.0, (1.0,)),))

    score_a = _score_team(plan_a, dispatch, rules)
    score_b = _score_team(plan_b, dispatch, rules)

    assert score_a.energy_cost == 26.0
    assert score_b.energy_cost == 34.0


def test_practice_submission_excludes_energy_purchase_cost_and_scored_submission_uses_joint_lmp():
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
        rules_version="test-v1",
        it_value_per_mwh=(1500.0,),
        energy_price_per_mwh=(999.0,),
    )

    practice = _score_team(plan, dispatch, rules, submission_number=1)
    scored = _score_team(plan, dispatch, rules, submission_number=2)

    assert practice.energy_cost == 0.0
    assert scored.energy_cost == 80.0
    assert scored.score == practice.score - 80.0


def test_revised_capex_and_connection_cost_are_capacity_based_and_storage_has_no_operating_cost():
    dispatch = SimpleNamespace(
        periods=(
            SimpleNamespace(
                period_index=0,
                submitted_load_mw_by_team={"team-a": 10.0},
                generator_dispatch_mw_by_team={"team-a": {"gen-a": 1.0}},
                grid_purchase_mw_by_team={"team-a": 8.0},
                grid_purchase_mw_by_team_by_bus={"team-a": {"B1": 8.0}},
                dcopf=SimpleNamespace(balance_dual_by_bus={"B1": 20.0}),
                storage_discharge_mw_by_team={"team-a": {"store-a": 10.0}},
            ),
        ),
    )
    plan = TeamPlan(
        "team-a",
        (ConnectionPlan("site-a", "B1", 10.0, (1.0,)),),
        generators=(GeneratorPlan("gen-a", "site-a", 2.0, 100.0),),
        storages=(StoragePlan("store-a", "site-a", 3.0, 12.0),),
    )
    rules = BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=(1500.0,),
        energy_price_per_mwh=(0.0,),
        generator_capital_cost_per_mw=1_300_000.0,
        site_cost_per_mw=20_000_000.0,
        storage_capital_cost_per_mw=350_000.0,
        storage_capital_cost_per_mwh=200_000.0,
    )

    score = _score_team(
        plan,
        dispatch,
        rules,
        submission_number=2,
        site_cost_connection_capacity_mw_by_team={"team-a": 10.0},
    )

    assert score.generator_capital_cost == 2_600_000.0
    assert score.storage_capital_cost == 3_450_000.0
    assert score.site_cost == 200_000_000.0
    assert not hasattr(score, "storage_operating_cost")


def test_load_scoring_rules_replaces_legacy_vectors_with_artifact(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text("bus_id\nB1\n", encoding="utf-8")
    npz_path = artifact / "economic_profiles.npz"
    import numpy as np
    np.savez(npz_path, lmp_per_mwh=np.ones((4368, 1)))
    digest = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    (artifact / "economic_profile_manifest.json").write_text(
        json.dumps({"profile_version": "lmp-v1", "horizon_hours": 4368, "bus_ids": ["B1"], "artifact_sha256": digest, "price_source": "test"}),
        encoding="utf-8",
    )
    (artifact / "economic_profile.json").write_text(
        json.dumps({"it_value_per_mwh": [100.0] * 4368, "it_value_provenance": {"horizon_hours": 4368}}),
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules_version": "test-v1", "it_value_per_mwh": [200.0] * 4368, "energy_price_per_mwh": [50.0] * 4368}), encoding="utf-8")

    rules = load_scoring_rules(rules_path, artifact_dir=artifact, horizon=4368)

    assert rules.economic_profiles is not None
    assert rules.economic_profiles.price_for_bus("B1", 0) == 1.0
    assert rules.it_value_per_mwh == ()
    assert rules.energy_price_per_mwh == ()


@pytest.mark.parametrize("year", [2027, 2029])
def test_nonleap_jan_jun_artifacts_load_and_score_at_final_hour(tmp_path, year):
    artifact = Path(__file__).parents[1] / "data" / "processed" / "activsg2000_scenarios_v1" / str(year) / "jan_jun"
    horizon = half_year_calendar(year, "jan_jun").horizon
    rules_path = tmp_path / f"rules-{year}.json"
    rules_path.write_text(json.dumps({"rules_version": "test-v1"}), encoding="utf-8")

    rules = load_scoring_rules(rules_path, artifact_dir=artifact)

    assert rules.economic_profiles is not None
    assert rules.economic_profiles.horizon_hours == 4344
    final_period = horizon - 1
    dispatch = SimpleNamespace(
        periods=(
            SimpleNamespace(
                period_index=final_period,
                submitted_load_mw_by_team={"team-a": 2.0},
                generator_dispatch_mw_by_team={"team-a": {}},
                grid_purchase_mw_by_team={"team-a": 2.0},
                grid_purchase_mw_by_team_by_bus={"team-a": {"1001": 2.0}},
                dcopf=SimpleNamespace(balance_dual_by_bus={}),
            ),
        ),
    )
    plan = TeamPlan("team-a", (ConnectionPlan("site-a", "1001", 2.0, (1.0,)),))

    score = _score_team(plan, dispatch, rules)

    assert score.it_value == pytest.approx(2.0 * 1500.0)
    assert score.energy_cost == pytest.approx(
        2.0 * rules.economic_profiles.price_for_bus("1001", final_period)
    )


def test_evaluate_csv_batch_rejects_explicit_empty_period_indices():
    with pytest.raises(ValueError, match="period_indices must be non-empty"):
        evaluate_csv_batch(
            (),
            artifact_dir="unused",
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            rules=_rules(),
            period_indices=(),
        )


def test_evaluate_csv_batch_rejects_duplicate_period_indices():
    with pytest.raises(ValueError, match="period_indices must not contain duplicates"):
        evaluate_csv_batch(
            (),
            artifact_dir="unused",
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            rules=_rules(),
            period_indices=(0, 0),
        )


def test_evaluate_csv_batch_rejects_out_of_range_period_indices_without_submissions():
    with pytest.raises(ValueError, match="period_indices must be less than horizon"):
        evaluate_csv_batch(
            (),
            artifact_dir="unused",
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            rules=_rules(),
            period_indices=(1,),
        )


def test_evaluate_csv_batch_rejects_non_integer_period_indices_without_submissions():
    with pytest.raises(ValueError, match="period_indices must contain integers"):
        evaluate_csv_batch(
            (),
            artifact_dir="unused",
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            rules=_rules(),
            period_indices=("0",),
        )


def test_half_year_two_maps_local_scoring_periods_to_source_artifact_rows(
    tmp_path, monkeypatch
):
    submission = tmp_path / "team-a.csv"
    with submission.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_CSV_COLUMNS)
        writer.writeheader()
        for period in range(1, 4417):
            writer.writerow(
                {
                    "team_id": "team-a",
                    "submission_id": "half-year-2",
                    "period": str(period),
                    "site_id": "PANHANDLE",
                    "load_mw": "2",
                    "generator_id": "",
                    "generator_capacity_mw": "",
                    "storage_id": "",
                    "storage_power_mw": "",
                    "storage_energy_mwh": "",
                }
            )

    rules = BatchScoringRules(
        rules_version="test-v1",
        it_value_per_mwh=(200.0,) * 4416,
        energy_price_per_mwh=(50.0,) * 4416,
    )
    observed_source_periods = []
    observed_profiles = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        observed_source_periods.extend(period_indices)
        observed_profiles.append(tuple(plans[0].connections[0].profile))
        return _fake_dispatch(
            tuple(sorted(plan.team_id for plan in plans)),
            period_indices=tuple(period_indices),
        )

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        submission,
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=4416,
        rules=rules,
    )

    assert observed_source_periods == list(range(4368, 8784))
    assert len(observed_profiles[0]) == 8784
    assert observed_profiles[0][4368] == 1.0
    assert observed_profiles[0][8783] == 1.0
    assert result.source_period_indices == tuple(range(4368, 8784))
    assert tuple(period.period_index for period in result.dispatch.periods) == tuple(range(4416))
    assert result.team_scores[0].served_load_mwh == 8832.0


def test_evaluate_csv_batch_accepts_explicit_source_period_indices(tmp_path, monkeypatch):
    submission = tmp_path / "team-a.csv"
    _write_submission(submission, "team-a")
    observed_source_periods = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        observed_source_periods.extend(period_indices)
        return _fake_dispatch(
            tuple(sorted(plan.team_id for plan in plans)),
            period_indices=tuple(period_indices),
        )

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        submission,
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
        period_indices=(0,),
        source_period_indices=(4368,),
    )

    assert observed_source_periods == [4368]
    assert result.source_period_indices == (4368,)
    assert result.dispatch.periods[0].period_index == 0


@pytest.mark.parametrize(
    ("source_period_indices", "message"),
    (
        ((4368, 4369), "same length"),
        ((4368, 4368), "duplicates"),
    ),
)
def test_evaluate_csv_batch_rejects_invalid_source_period_mapping(
    source_period_indices, message
):
    with pytest.raises(ValueError, match=message):
        if message == "duplicates":
            batch_evaluation._validate_period_mapping((0, 1), source_period_indices)
        else:
            evaluate_csv_batch(
                (),
                artifact_dir="unused",
                region_to_source_bus={"PANHANDLE": "3002041"},
                horizon=1,
                rules=_rules(),
                period_indices=(0,),
                source_period_indices=source_period_indices,
            )


def test_write_batch_outputs_matches_auditable_output_contract(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)
    output_dir = tmp_path / "output"
    _write_outputs(result, output_dir)

    assert {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    } == {
        "leaderboard.csv",
        "submission_manifest.csv",
        "evaluation.json",
        "evaluation_sha256.txt",
        "diagnostics/team-a.json",
        "diagnostics/team-b.json",
        "dispatch/period_0.json",
    }

    evaluation_text = (output_dir / "evaluation.json").read_text(encoding="utf-8")
    assert evaluation_text.endswith("\n")
    payload = json.loads(evaluation_text)
    assert {
        "run_id",
        "rules_version",
        "scenario_version",
        "evaluator_version",
        "input_sha256_by_team",
        "competition_scope",
        "isolation_events",
        "output_sha256",
        "team_scores",
        "team_diagnostics",
        "periods",
    } <= payload.keys()
    assert payload["output_sha256"] == dict(sorted(payload["output_sha256"].items()))
    assert all("storage_operating_cost" not in score for score in payload["team_scores"])
    assert "storage_operating_cost_per_mwh" not in payload["rules"]
    assert set(payload["output_sha256"]) == {
        "leaderboard.csv",
        "submission_manifest.csv",
        "diagnostics/team-a.json",
        "diagnostics/team-b.json",
        "dispatch/period_0.json",
    }
    assert (output_dir / "leaderboard.csv").read_text(encoding="utf-8").splitlines()[0] == (
        "team_id,rank,valid,feasible,score,served_load_mwh,it_value,energy_cost,"
        "generator_operating_cost,generator_capital_cost,site_cost,storage_capital_cost,"
        "competition_scope,failure_reason"
    )
    assert payload["output_sha256"]["dispatch/period_0.json"] == hashlib.sha256(
        (output_dir / "dispatch/period_0.json").read_bytes()
    ).hexdigest()
    assert json.loads((output_dir / "diagnostics/team-a.json").read_text())[
        "team_score"
    ]["team_id"] == "team-a"
    period_payload = json.loads((output_dir / "dispatch/period_0.json").read_text())
    assert period_payload["solver_status"] == "optimal"
    assert period_payload["objective_cost"] == 0.0
    assert period_payload["submitted_load_mw_by_team"] == {"team-a": 2.0, "team-b": 2.0}
    assert period_payload["generator_dispatch_mw_by_team"]["team-a"] == {"gen-1": 0.0}
    assert period_payload["flow_mw_by_branch"] == {"branch-1": 2.0}
    assert period_payload["binding_diagnostics"]["balance_dual_by_bus"] == {"bus-1": 50.0}
    assert payload["periods"] == [period_payload]
    assert payload["team_diagnostics"]["team-a"]["team_score"]["team_id"] == "team-a"

    evaluation_hash = hashlib.sha256((output_dir / "evaluation.json").read_bytes()).hexdigest()
    assert (output_dir / "evaluation_sha256.txt").read_text(encoding="utf-8") == (
        evaluation_hash + "\n"
    )


def test_write_batch_outputs_is_byte_deterministic_across_repeat_runs(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"
    _write_outputs(result, first_output)
    _write_outputs(result, second_output)

    relative_files = (
        "leaderboard.csv",
        "submission_manifest.csv",
        "evaluation.json",
        "evaluation_sha256.txt",
        "diagnostics/team-a.json",
        "diagnostics/team-b.json",
        "dispatch/period_0.json",
    )
    for relative_path in relative_files:
        assert (first_output / relative_path).read_bytes() == (
            second_output / relative_path
        ).read_bytes()

    first_payload = json.loads((first_output / "evaluation.json").read_text())
    assert first_payload["run_id"] == json.loads(
        (second_output / "evaluation.json").read_text()
    )["run_id"]


def test_write_batch_outputs_rerun_replaces_stale_generated_artifacts(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    output_dir = tmp_path / "output"
    first_result = _evaluate_paths(tmp_path, monkeypatch, (first, second), dispatch)
    _write_outputs(first_result, output_dir, period_indices=(0, 1))
    (output_dir / "diagnostics" / "keep.txt").write_text("keep", encoding="utf-8")
    (output_dir / "dispatch" / "keep.json").write_text("keep", encoding="utf-8")

    second_result = _evaluate_paths(tmp_path, monkeypatch, (first,), dispatch)
    _write_outputs(second_result, output_dir, period_indices=(0,))

    payload = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert "diagnostics/team-b.json" not in payload["output_sha256"]
    assert "dispatch/period_1.json" not in payload["output_sha256"]
    assert not (output_dir / "diagnostics" / "team-b.json").exists()
    assert not (output_dir / "dispatch" / "period_1.json").exists()
    assert (output_dir / "diagnostics" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (output_dir / "dispatch" / "keep.json").read_text(encoding="utf-8") == "keep"


def test_write_batch_outputs_preserves_unrelated_diagnostic_json(tmp_path, monkeypatch):
    submission = tmp_path / "team-a.csv"
    _write_submission(submission, "team-a")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result = _evaluate_paths(tmp_path, monkeypatch, (submission,), dispatch)
    output_dir = tmp_path / "output"
    unrelated = output_dir / "diagnostics" / "unrelated.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text('{"source":"external"}\n', encoding="utf-8")

    _write_outputs(result, output_dir)

    assert unrelated.read_text(encoding="utf-8") == '{"source":"external"}\n'


def test_effective_scoring_configuration_is_deterministic_and_changes_run_id(
    tmp_path, monkeypatch
):
    submission = tmp_path / "team-a.csv"
    _write_submission(submission, "team-a")

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result_at_100 = _evaluate_paths(
        tmp_path,
        monkeypatch,
        (submission,),
        dispatch,
        default_generator_cost_per_mwh=100.0,
    )
    result_at_200 = _evaluate_paths(
        tmp_path,
        monkeypatch,
        (submission,),
        dispatch,
        default_generator_cost_per_mwh=200.0,
    )
    first_output = tmp_path / "first-output"
    repeat_output = tmp_path / "repeat-output"
    second_output = tmp_path / "second-output"
    _write_outputs(result_at_100, first_output)
    _write_outputs(result_at_100, repeat_output)
    _write_outputs(result_at_200, second_output)

    first_payload = json.loads((first_output / "evaluation.json").read_text(encoding="utf-8"))
    repeat_payload = json.loads((repeat_output / "evaluation.json").read_text(encoding="utf-8"))
    second_payload = json.loads((second_output / "evaluation.json").read_text(encoding="utf-8"))

    assert first_payload["evaluation_metadata"]["scoring_configuration"][
        "default_generator_cost_per_mwh"
    ] == 100.0
    assert second_payload["evaluation_metadata"]["scoring_configuration"][
        "default_generator_cost_per_mwh"
    ] == 200.0
    assert first_payload["run_id"] == repeat_payload["run_id"]
    assert first_payload["run_id"] != second_payload["run_id"]


def test_diagnostic_filenames_are_bounded_safe_and_deterministic(tmp_path, monkeypatch):
    team_id = "team/unsafe:" + ("very-long-identifier-" * 20)
    submission = tmp_path / "unsafe.csv"
    _write_submission(submission, team_id)

    def dispatch(_artifact_dir, plans, *, period_indices):
        return _fake_dispatch(tuple(sorted(plan.team_id for plan in plans)))

    result = _evaluate_paths(tmp_path, monkeypatch, (submission,), dispatch)
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"
    _write_outputs(result, first_output)
    _write_outputs(result, second_output)

    first_name = next((first_output / "diagnostics").glob("*.json")).name
    second_name = next((second_output / "diagnostics").glob("*.json")).name
    assert first_name == second_name
    assert len(first_name) <= 120
    assert "/" not in first_name
    assert ":" not in first_name
    assert first_name.removesuffix(".json").rsplit("--", 1)[1].isalnum()


def test_evaluation_hashes_every_discovered_file_without_duplicate_team_overwrite(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    malformed = tmp_path / "malformed.csv"
    _write_submission(first, "team-duplicate", submission_id="first")
    _write_submission(second, "team-duplicate", submission_id="second")
    malformed.write_bytes(b"not,a,valid,submission\n")

    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        (first, second, malformed),
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
    )

    expected = {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (first, second, malformed)
    }
    assert result.input_sha256_by_submission == expected
    assert len(result.rejected_submissions) == 3
    assert len({record.submission_key for record in result.rejected_submissions}) == 3


def test_batch_evaluation_rejects_missing_production_catalog(tmp_path):
    submission = tmp_path / "team-a.csv"
    _write_submission(submission, "team-a")

    with pytest.raises(ValueError, match="catalog"):
        evaluate_csv_batch(
            (submission,),
            artifact_dir=tmp_path / "missing-artifact",
            region_to_source_bus={"PANHANDLE": "3002041"},
            horizon=1,
            rules=_rules(),
        )


def test_submission_manifest_and_diagnostics_are_unique_for_duplicate_team_ids(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_submission(first, "team-duplicate", submission_id="first")
    _write_submission(second, "team-duplicate", submission_id="second")

    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        (first, second),
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
    )
    output_dir = tmp_path / "output"
    _write_outputs(result, output_dir)

    manifest_rows = list(
        csv.DictReader((output_dir / "submission_manifest.csv").open(newline="", encoding="utf-8"))
    )
    diagnostic_paths = sorted((output_dir / "diagnostics").glob("*.json"))
    assert len(manifest_rows) == 2
    assert len({row["submission_key"] for row in manifest_rows}) == 2
    assert len(diagnostic_paths) == 2
    assert len({path.name for path in diagnostic_paths}) == 2
    payload = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert len(payload["rejected_submissions"]) == 2
    assert len(payload["team_diagnostics"]) == 2


def test_individual_fallback_retains_dispatch_records_for_period_audits(tmp_path, monkeypatch):
    first = tmp_path / "team-a.csv"
    second = tmp_path / "team-b.csv"
    _write_submission(first, "team-a")
    _write_submission(second, "team-b")
    calls = []

    # Projection is explicitly unavailable in this test. The joint attempt
    # and two legacy subset attempts fail before individual fallback.
    failures_before_individual = 3

    def dispatch(_artifact_dir, plans, *, period_indices):
        team_ids = tuple(sorted(plan.team_id for plan in plans))
        calls.append(team_ids)
        if len(calls) <= failures_before_individual:
            raise DCOPFInfeasible("synthetic shared overload")
        return _fake_dispatch(team_ids)

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    monkeypatch.setattr(
        batch_evaluation,
        "_fair_continuous_projection",
        lambda *args, **kwargs: None,
    )
    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        (first, second),
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
    )
    output_dir = tmp_path / "output"
    _write_outputs(result, output_dir)

    assert result.dispatch is None
    assert len(result.dispatch_records) == 5
    assert (output_dir / "dispatch" / "period_0.json").exists()
    period_payload = json.loads((output_dir / "dispatch" / "period_0.json").read_text())
    assert period_payload["submitted_load_mw_by_team"] == {"team-a": 2.0, "team-b": 2.0}
    assert period_payload["solver_status"] == "optimal"
    assert "curtailment" not in period_payload
    evaluation_payload = json.loads((output_dir / "evaluation.json").read_text())
    assert evaluation_payload["evaluation_metadata"]["competition_scope"] == "individual_fallback"
    assert evaluation_payload["evaluation_metadata"]["subset_evaluations"]
    assert len(evaluation_payload["accepted_submissions"]) == 2


def test_all_rejected_batch_still_emits_period_audit_and_explicit_records(tmp_path):
    malformed = tmp_path / "malformed.csv"
    malformed.write_text("not,a,valid,submission\n", encoding="utf-8")

    artifact_dir = _write_artifact_catalogs(tmp_path)
    result = evaluate_csv_batch(
        (malformed,),
        artifact_dir=artifact_dir,
        region_to_source_bus={"PANHANDLE": "3002041"},
        horizon=1,
        rules=_rules(),
    )
    output_dir = tmp_path / "output"
    _write_outputs(result, output_dir)

    period_payload = json.loads((output_dir / "dispatch" / "period_0.json").read_text())
    evaluation_payload = json.loads((output_dir / "evaluation.json").read_text())
    manifest_rows = list(
        csv.DictReader((output_dir / "submission_manifest.csv").open(newline="", encoding="utf-8"))
    )
    assert period_payload["solver_status"] == "not_run"
    assert period_payload["objective_cost"] is None
    assert period_payload["submitted_load_mw_by_team"] == {}
    assert evaluation_payload["accepted_submissions"] == []
    assert len(evaluation_payload["rejected_submissions"]) == 1
    assert {
        "evaluation": evaluation_payload["rejected_submissions"][0]["identity_is_fallback"],
        "manifest": manifest_rows[0]["identity_is_fallback"],
    } == {"evaluation": True, "manifest": "True"}
    assert evaluation_payload["evaluation_metadata"]["subset_evaluations"]
    assert evaluation_payload["aggregate_diagnostics"]["dispatch_count"] == 0
