from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ercot_game.asset_state import AssetStateError
from ercot_game.game_state import GameState
from ercot_game.game_state_store import GameStateStore
from ercot_game.batch_evaluation import CapitalCostScope, ProjectionDiagnostics, TeamScore
from ercot_game.models import ConnectionPlan, GeneratorPlan, SitePlan, StoragePlan, TeamPlan
from ercot_game import round_coordinator
from ercot_game.round_coordinator import (
    ClassroomRoundInput,
    RoundCoordinatorError,
    evaluate_classroom,
    evaluate_round,
)


def _plan(team_id: str = "team-a", bus_id: str = "b0001") -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(ConnectionPlan("site-1", bus_id, 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-1", "site-1", 10.0, 16.0),),
    )


def _asset_plan(team_id: str = "team-a") -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-old", "site-old", 10.0, 16.0),),
        storages=(StoragePlan("store-old", "site-old", 5.0, 20.0),),
    )


@dataclass(frozen=True)
class _MinimalDispatch:
    periods: tuple = (SimpleNamespace(storage_soc_mwh_by_team={}),)


@dataclass(frozen=True)
class _UsableEvaluation:
    rejected_submissions: tuple = ()
    dispatch: object = field(default_factory=_MinimalDispatch)
    projection_diagnostics: ProjectionDiagnostics | None = None
    team_scores: tuple = field(
        default_factory=lambda: (
            TeamScore(
                team_id="team-a",
                rank=1,
                valid=True,
                feasible=True,
                score=10.0,
                served_load_mwh=1.0,
                it_value=10.0,
                energy_cost=0.0,
                generator_operating_cost=0.0,
                generator_capital_cost=0.0,
                site_cost=0.0,
                competition_scope="joint",
            ),
        )
    )


def _loader(plans):
    def load(*args, **kwargs):
        return tuple(plans)

    return load


def test_successful_round_commits_advanced_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen["called"] = True
        return _UsableEvaluation()

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((_plan(),)),
        evaluator=evaluator,
    )

    assert seen["called"] is True
    assert result.state.current_round_key == "2027_jan_jun"
    assert GameStateStore(state_path).load().current_round_key == "2027_jan_jun"


def test_round_commit_records_final_storage_soc_and_reuses_it_next_round(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    seen: list[object] = []

    def evaluator(*args, **kwargs):
        seen.append(kwargs["plan_overrides"])
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(SimpleNamespace(storage_soc_mwh_by_team={"team-a": {"store-old": 15.0}}),)
            )
        )

    evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((_asset_plan(),)),
        evaluator=evaluator,
    )
    evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((TeamPlan("team-a", connections=()),)),
        evaluator=evaluator,
    )

    assert seen[1]["team-a"].storages[0].initial_state_of_charge_mwh == 15.0


def test_partial_dispatch_carries_forward_soc_for_absent_storage_team(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance("2027_jan_jun", (_asset_plan("team-b"),))
    prior = replace(
        prior,
        asset_ledger=prior.asset_ledger.update_storage_soc(
            {"team-b": {"store-old": 15.0}}
        ),
    )
    GameStateStore(state_path).save(prior)

    def evaluator(*args, **kwargs):
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                team_ids=("team-a",),
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"store-old": 15.0}}
                    ),
                ),
            )
        )

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((_asset_plan("team-a"), TeamPlan("team-b", connections=()))),
        evaluator=evaluator,
    )

    assert result.state.asset_ledger.team("team-a").storages["store-old"].state_of_charge_mwh == 15.0
    assert result.state.asset_ledger.team("team-b").storages["store-old"].state_of_charge_mwh == 15.0


def test_dispatched_storage_team_still_requires_final_soc_report(tmp_path) -> None:
    state_path = tmp_path / "state.json"

    def evaluator(*args, **kwargs):
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                team_ids=("team-a",),
                periods=(SimpleNamespace(storage_soc_mwh_by_team={}),),
            )
        )

    with pytest.raises(RoundCoordinatorError, match="missing final storage state of charge for team team-a"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_asset_plan("team-a"), TeamPlan("team-b", connections=()))),
            evaluator=evaluator,
        )

    assert not state_path.exists()


@pytest.mark.parametrize(
    "reported, message",
    [
        ({}, "missing final storage state of charge"),
        ({"team-a": {"store-old": 21.0}}, "final storage state of charge"),
        ({"team-a": {"store-old": 9.0}}, "must be at least half"),
    ],
)
def test_invalid_final_storage_soc_leaves_state_unchanged(tmp_path, reported, message) -> None:
    state_path = tmp_path / "state.json"

    def evaluator(*args, **kwargs):
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(SimpleNamespace(storage_soc_mwh_by_team=reported),)
            )
        )

    with pytest.raises(RoundCoordinatorError, match=message):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_asset_plan(),)),
            evaluator=evaluator,
        )

    assert not state_path.exists()


def test_invalid_final_storage_soc_preserves_existing_state_file(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    store = GameStateStore(state_path)
    store.save(GameState.new().advance("2027_jan_jun", (_asset_plan(),)))
    before = state_path.read_bytes()

    def evaluator(*args, **kwargs):
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"store-old": 21.0}}
                    ),
                )
            )
        )

    with pytest.raises(RoundCoordinatorError, match="exceeds energy capacity"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jul_dec",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((TeamPlan("team-a", connections=()),)),
            evaluator=evaluator,
        )

    assert state_path.read_bytes() == before


def test_evaluation_failure_does_not_change_existing_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance("2027_jan_jun", (_plan(),))
    store = GameStateStore(state_path)
    store.save(prior)
    before = state_path.read_bytes()

    def evaluator(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    with pytest.raises(RuntimeError, match="dispatch failed"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jul_dec",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(),)),
            evaluator=evaluator,
        )

    assert state_path.read_bytes() == before
    assert store.load().current_round_key == "2027_jan_jun"


def test_plan_loader_failure_does_not_create_state_file(tmp_path) -> None:
    state_path = tmp_path / "state.json"

    def loader(*args, **kwargs):
        raise RoundCoordinatorError("invalid submission")

    with pytest.raises(RoundCoordinatorError, match="invalid submission"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=loader,
            evaluator=lambda *args, **kwargs: _UsableEvaluation(),
        )

    assert not state_path.exists()


def test_asset_conflict_does_not_change_existing_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    store = GameStateStore(state_path)
    store.save(GameState.new().advance("2027_jan_jun", (_plan(),)))
    before = state_path.read_bytes()

    with pytest.raises(AssetStateError):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jul_dec",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(bus_id="b0002"),)),
            evaluator=lambda *args, **kwargs: _UsableEvaluation(),
        )

    assert state_path.read_bytes() == before


def test_rejected_result_does_not_commit_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    rejected = _UsableEvaluation(rejected_submissions=(object(),), dispatch=None)

    with pytest.raises(RoundCoordinatorError, match="usable dispatch"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(),)),
            evaluator=lambda *args, **kwargs: rejected,
        )

    assert not state_path.exists()


@pytest.mark.parametrize("dispatch", (object(), SimpleNamespace(periods=None)))
def test_evaluation_without_period_collection_does_not_commit_state(tmp_path, dispatch) -> None:
    state_path = tmp_path / "state.json"
    evaluation = _UsableEvaluation(dispatch=dispatch)

    with pytest.raises(RoundCoordinatorError, match="usable dispatch"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(),)),
            evaluator=lambda *args, **kwargs: evaluation,
        )

    assert not state_path.exists()


def test_evaluation_with_empty_period_collection_does_not_commit_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    evaluation = _UsableEvaluation(dispatch=SimpleNamespace(periods=()))

    with pytest.raises(RoundCoordinatorError, match="usable dispatch"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(),)),
            evaluator=lambda *args, **kwargs: evaluation,
        )

    assert not state_path.exists()


def test_valid_teams_commit_when_rejected_team_is_isolated(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    evaluation = _UsableEvaluation(rejected_submissions=(object(),))

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((_plan(),)),
        evaluator=lambda *args, **kwargs: evaluation,
    )

    assert result.state.current_round_key == "2027_jan_jun"
    assert GameStateStore(state_path).load().current_round_key == "2027_jan_jun"


def test_valid_teams_commit_even_when_evaluation_reports_rejected_team(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    rejected = _UsableEvaluation(rejected_submissions=(object(),))

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=lambda *args, **kwargs: (_plan(),),
        evaluator=lambda *args, **kwargs: rejected,
    )

    assert result.state.current_round_key == "2027_jan_jun"
    assert GameStateStore(state_path).load().current_round_key == "2027_jan_jun"


def test_projection_diagnostics_cannot_add_rejected_team_to_round_state(tmp_path) -> None:
    state_path = tmp_path / "state.json"
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

    with pytest.raises(RoundCoordinatorError, match="rejected or unknown team"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan("team-valid"),)),
            evaluator=lambda *args, **kwargs: _UsableEvaluation(
                projection_diagnostics=diagnostics
            ),
        )

    assert not state_path.exists()


def test_default_plan_loader_keeps_accepted_plans_when_one_file_is_rejected(monkeypatch, tmp_path) -> None:
    accepted = SimpleNamespace(submission=SimpleNamespace(plan=_plan()))
    rejected = SimpleNamespace(team_id="team-b", failure_reason="bad CSV")
    monkeypatch.setattr(round_coordinator, "load_bus_ids", lambda _path: frozenset({"b0001"}))
    monkeypatch.setattr(
        round_coordinator,
        "_collect_evaluation_inputs",
        lambda *args, **kwargs: SimpleNamespace(
            accepted=(accepted,),
            rejected=(rejected,),
        ),
    )

    plans = round_coordinator._default_plan_loader(
        tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=SimpleNamespace(default_generator_cost_per_mwh=16.0),
    )

    assert plans == (_plan(),)


def test_later_round_materializes_prior_assets_and_scopes_new_capital_costs(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance("2027_jan_jun", (_asset_plan(),))
    GameStateStore(state_path).save(prior)
    current = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-new", "b0001", 30.0, (1.0,)),),
        generators=(GeneratorPlan("gen-new", "site-new", 8.0, 16.0),),
        storages=(StoragePlan("store-new", "site-new", 4.0, 16.0),),
    )
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={
                            "team-a": {"store-new": 8.0, "store-old": 10.0},
                        }
                    ),
                )
            )
        )

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((current,)),
        evaluator=evaluator,
    )

    overrides = seen["plan_overrides"]
    assert overrides["team-a"].generators == (
        GeneratorPlan("gen-new", "site-new", 8.0, 16.0),
        GeneratorPlan("gen-old", "site-old", 10.0, 16.0),
    )
    assert overrides["team-a"].storages == (
        StoragePlan("store-new", "site-new", 4.0, 16.0),
        StoragePlan("store-old", "site-old", 5.0, 20.0, 10.0),
    )
    assert seen["capital_cost_scope"] == CapitalCostScope(
        generator_ids_by_team={"team-a": {"gen-old", "gen-new"}},
        storage_ids_by_team={"team-a": {"store-old", "store-new"}},
    )
    assert seen["site_cost_connection_count_by_team"] == {"team-a": 1}
    assert seen["site_cost_connection_capacity_mw_by_team"] == {"team-a": 30.0}
    assert seen["submission_number"] == 2
    assert set(result.state.asset_ledger.team("team-a").generators) == {
        "generator:team-a:site-new",
        "generator:team-a:site-old",
    }


def test_label_rename_at_same_site_does_not_recharge_capital_cost(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance("2027_jan_jun", (_asset_plan(),))
    GameStateStore(state_path).save(prior)
    current = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-renamed", "site-old", 10.0, 16.0),),
        storages=(StoragePlan("store-renamed", "site-old", 5.0, 20.0),),
    )
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"store-renamed": 10.0}},
                    ),
                ),
            ),
        )

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((current,)),
        evaluator=evaluator,
    )

    assert seen["capital_cost_scope"] == CapitalCostScope(
        generator_ids_by_team={"team-a": {"gen-renamed"}},
        storage_ids_by_team={"team-a": {"store-renamed"}},
    )
    assert seen["plan_overrides"]["team-a"].generators[0].generator_id == "gen-renamed"
    assert result.state.asset_ledger.team("team-a").generators["gen-renamed"].asset_key == (
        "generator:team-a:site-old"
    )


def test_reusing_label_at_new_site_charges_capital_cost_for_new_asset(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance(
        "2027_jan_jun",
        (
            TeamPlan(
                team_id="team-a",
                connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0,)),),
                generators=(GeneratorPlan("gen-old", "site-old", 10.0, 16.0),),
            ),
        ),
    )
    GameStateStore(state_path).save(prior)
    current = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-new", "b0002", 30.0, (1.0,)),),
        generators=(GeneratorPlan("gen-old", "site-new", 8.0, 16.0),),
        storages=(StoragePlan("store-old", "site-new", 4.0, 16.0),),
    )
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"store-old": 8.0}},
                    ),
                ),
            ),
        )

    evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((current,)),
        evaluator=evaluator,
    )

    assert seen["capital_cost_scope"] == CapitalCostScope(
        generator_ids_by_team={
            "team-a": {"generator:team-a:site-old", "generator:team-a:site-new"}
        },
        storage_ids_by_team={"team-a": {"store-old"}},
    )


def test_site_only_plan_uses_site_cost_scope_and_constant_materialized_load(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-1", "b0001", 40.0),),
    )
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation()

    evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=2,
        rules=object(),
        plan_loader=_loader((plan,)),
        evaluator=evaluator,
    )

    assert seen["site_cost_connection_count_by_team"] == {"team-a": 1}
    assert seen["plan_overrides"]["team-a"].connections == (
        ConnectionPlan("site-1", "b0001", 40.0, (1.0, 1.0)),
    )


def test_site_plan_round_two_keeps_omitted_site_active_and_does_not_recharge_assets(
    tmp_path,
) -> None:
    state_path = tmp_path / "state.json"
    first_plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-a", "b0001", 40.0, 10.0, 5.0, 20.0),),
    )
    second_plan = TeamPlan(team_id="team-a", sites=())
    seen: list[dict[str, object]] = []

    def evaluator(*args, **kwargs):
        seen.append(kwargs)
        storage_ids = [storage.storage_id for storage in kwargs["plan_overrides"]["team-a"].storages]
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={
                            "team-a": {storage_id: 15.0 for storage_id in storage_ids}
                        }
                    ),
                )
            )
        )

    evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=3,
        rules=object(),
        plan_loader=_loader((first_plan,)),
        evaluator=evaluator,
    )
    evaluate_round(
        state_path=state_path,
        round_key="2027_jul_dec",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=3,
        rules=object(),
        plan_loader=_loader((second_plan,)),
        evaluator=evaluator,
    )

    assert seen[1]["plan_overrides"]["team-a"].connections == (
        ConnectionPlan("site-a", "b0001", 40.0, (1.0, 1.0, 1.0)),
    )
    assert seen[1]["plan_overrides"]["team-a"].storages[0].initial_state_of_charge_mwh == 15.0
    assert seen[1]["capital_cost_scope"] == CapitalCostScope(
        generator_ids_by_team={"team-a": {"generator:team-a:site-a"}},
        storage_ids_by_team={"team-a": {"storage:team-a:site-a"}},
    )


@pytest.mark.parametrize(
    ("round_key", "prior_rounds"),
    (
        ("2028_jan_jun", ("2027_jan_jun", "2027_jul_dec")),
        (
            "2029_jan_jun",
            ("2027_jan_jun", "2027_jul_dec", "2028_jan_jun", "2028_jul_dec"),
        ),
    ),
)
def test_first_round_of_new_week_resets_before_materialization_and_soc_write(
    tmp_path,
    round_key,
    prior_rounds,
) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new()
    for index, previous_round_key in enumerate(prior_rounds):
        plans = (_asset_plan(),) if index % 2 == 0 else (TeamPlan("team-a", connections=()),)
        prior = prior.advance(previous_round_key, plans)
    prior = replace(
        prior,
        asset_ledger=prior.asset_ledger.update_storage_soc(
            {"team-a": {"store-old": 15.0}},
        ),
    )
    GameStateStore(state_path).save(prior)
    current = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-new", "b0002", 30.0, (1.0,)),),
        storages=(StoragePlan("store-new", "site-new", 4.0, 16.0),),
    )
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"store-new": 8.0}},
                    ),
                ),
            ),
        )

    result = evaluate_round(
        state_path=state_path,
        round_key=round_key,
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((current,)),
        evaluator=evaluator,
    )

    materialized = seen["plan_overrides"]["team-a"]
    assert [storage.storage_id for storage in materialized.storages] == ["store-new"]
    assert result.prior_state.asset_ledger.team_ids() == ()
    assert result.state.asset_ledger.team("team-a").storages["store-new"].state_of_charge_mwh == 8.0
    with pytest.raises(KeyError):
        result.state.asset_ledger.team("team-a").storages["store-old"]


def test_rejected_team_is_not_materialized_or_registered(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    valid_plan = _plan("team-valid")
    rejected_plan = _plan("team-rejected")
    seen: dict[str, object] = {}

    def evaluator(*args, **kwargs):
        seen.update(kwargs)
        return _UsableEvaluation(rejected_submissions=(object(),))

    result = evaluate_round(
        state_path=state_path,
        round_key="2027_jan_jun",
        submissions=tmp_path / "submissions",
        artifact_dir=tmp_path / "artifacts",
        horizon=1,
        rules=object(),
        plan_loader=_loader((valid_plan,)),
        evaluator=evaluator,
    )

    assert set(seen["plan_overrides"]) == {"team-valid"}
    assert result.state.asset_ledger.team_ids() == ("team-valid",)
    assert "team-rejected" not in seen["capital_cost_scope"].generator_ids_by_team


def test_legacy_evaluator_without_stage_six_kwargs_is_rejected_before_call(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    called: dict[str, object] = {"value": False}

    def legacy_evaluator(submissions, *, artifact_dir, horizon, rules):
        called["value"] = True
        return _UsableEvaluation()

    with pytest.raises(RoundCoordinatorError, match="Stage 6-aware evaluator"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jan_jun",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((_plan(),)),
            evaluator=legacy_evaluator,
        )

    assert called["value"] is False
    assert not state_path.exists()


def test_materialization_evaluation_failure_leaves_existing_state_unchanged(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    prior = GameState.new().advance("2027_jan_jun", (_asset_plan(),))
    store = GameStateStore(state_path)
    store.save(prior)
    before = state_path.read_bytes()

    def evaluator(*args, **kwargs):
        assert "plan_overrides" in kwargs
        raise RuntimeError("materialized dispatch failed")

    with pytest.raises(RuntimeError, match="materialized dispatch failed"):
        evaluate_round(
            state_path=state_path,
            round_key="2027_jul_dec",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=_loader((TeamPlan("team-a", connections=()),)),
            evaluator=evaluator,
        )

    assert state_path.read_bytes() == before
    assert store.load().current_round_key == "2027_jan_jun"


def test_classroom_coordinator_evaluates_all_six_rounds_and_ranks_only_scored_weeks(tmp_path) -> None:
    state_path = tmp_path / "classroom-state.json"
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / round_key,
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for round_key in (
            "2027_jan_jun",
            "2027_jul_dec",
            "2028_jan_jun",
            "2028_jul_dec",
            "2029_jan_jun",
            "2029_jul_dec",
        )
    )

    def evaluator(*args, **kwargs):
        return _UsableEvaluation()

    result = evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_plan(),)),
        evaluator=evaluator,
    )

    assert [(item.week_id, item.submission_number) for item in result.submissions] == [
        ("week-1", 1),
        ("week-1", 2),
        ("week-2", 1),
        ("week-2", 2),
        ("week-3", 1),
        ("week-3", 2),
    ]
    assert [item.feedback_only for item in result.submissions] == [True, True, False, False, False, False]
    assert [item.ledger_reset for item in result.submissions] == [False, False, True, False, True, False]
    assert [ranking.week_id for ranking in result.weekly_rankings] == ["week-1", "week-2", "week-3"]
    assert result.weekly_rankings[0].rankings == ()
    assert result.weekly_rankings[0].practice_feedback[0].team_id == "team-a"
    assert all(ranking.rankings for ranking in result.weekly_rankings[1:])
    assert {score.team_id for score in result.final_rankings} == {"team-a"}
    assert result.final_rankings[0].rank == 1
    assert result.state.next_round_key is None


def test_classroom_resume_starts_at_persisted_next_round_without_replay(tmp_path) -> None:
    state_path = tmp_path / "classroom-state.json"
    round_keys = (
        "2027_jan_jun",
        "2027_jul_dec",
        "2028_jan_jun",
        "2028_jul_dec",
        "2029_jan_jun",
        "2029_jul_dec",
    )
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / f"submissions-{index}",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for index, round_key in enumerate(round_keys)
    )
    seen: list[str] = []
    calls = 0

    def interrupted_evaluator(*args, **kwargs):
        nonlocal calls
        calls += 1
        seen.append(Path(args[0]).name)
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return _UsableEvaluation()

    with pytest.raises(RuntimeError, match="simulated interruption"):
        evaluate_classroom(
            state_path=state_path,
            rounds=rounds,
            plan_loader=_loader((_plan(),)),
            evaluator=interrupted_evaluator,
        )

    resumed: list[str] = []

    def resumed_evaluator(*args, **kwargs):
        resumed.append(Path(args[0]).name)
        return _UsableEvaluation()

    result = evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_plan(),)),
        evaluator=resumed_evaluator,
    )

    assert seen == ["submissions-0", "submissions-1"]
    assert resumed == [f"submissions-{index}" for index in range(1, 6)]
    assert result.state.next_round_key is None


def test_classroom_resume_is_idempotent_after_all_rounds_are_complete(tmp_path) -> None:
    state_path = tmp_path / "classroom-state.json"
    round_keys = (
        "2027_jan_jun",
        "2027_jul_dec",
        "2028_jan_jun",
        "2028_jul_dec",
        "2029_jan_jun",
        "2029_jul_dec",
    )
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / round_key,
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for round_key in round_keys
    )
    evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_plan(),)),
        evaluator=lambda *args, **kwargs: _UsableEvaluation(),
    )
    complete_state = GameStateStore(state_path).load()

    def must_not_run(*args, **kwargs):
        raise AssertionError("completed classroom state was replayed")

    result = evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_plan(),)),
        evaluator=must_not_run,
    )

    assert result.submissions == ()
    assert result.state == complete_state


def test_classroom_capital_scope_charges_assets_once_per_weekly_match(tmp_path) -> None:
    state_path = tmp_path / "classroom-state.json"
    round_keys = (
        "2027_jan_jun",
        "2027_jul_dec",
        "2028_jan_jun",
        "2028_jul_dec",
        "2029_jan_jun",
        "2029_jul_dec",
    )
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / round_key,
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for round_key in round_keys
    )
    scopes = []

    def evaluator(*args, **kwargs):
        scopes.append(kwargs["capital_cost_scope"])
        plan = kwargs["plan_overrides"]["team-a"]
        return _UsableEvaluation(
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={
                            "team-a": {
                                storage.storage_id: storage.energy_mwh * 0.5
                                for storage in plan.storages
                            }
                        }
                    ),
                )
            )
        )

    evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_asset_plan(),)),
        evaluator=evaluator,
    )

    assert [scope.generator_ids_by_team for scope in scopes] == [
        {"team-a": {"gen-old"}},
        {"team-a": {"gen-old"}},
        {"team-a": {"gen-old"}},
        {"team-a": {"gen-old"}},
        {"team-a": {"gen-old"}},
        {"team-a": {"gen-old"}},
    ]
    assert [scope.storage_ids_by_team for scope in scopes] == [
        {"team-a": {"store-old"}},
        {"team-a": {"store-old"}},
        {"team-a": {"store-old"}},
        {"team-a": {"store-old"}},
        {"team-a": {"store-old"}},
        {"team-a": {"store-old"}},
    ]


def test_second_submission_is_rejected_before_loading_or_evaluating_without_predecessor(tmp_path) -> None:
    called = {"loader": False, "evaluator": False}

    def loader(*args, **kwargs):
        called["loader"] = True
        return (_plan(),)

    def evaluator(*args, **kwargs):
        called["evaluator"] = True
        return _UsableEvaluation()

    with pytest.raises(RoundCoordinatorError, match="submission 1.*week-1"):
        evaluate_round(
            state_path=tmp_path / "missing-state.json",
            round_key="2027_jul_dec",
            submissions=tmp_path / "submissions",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
            plan_loader=loader,
            evaluator=evaluator,
        )

    assert called == {"loader": False, "evaluator": False}


def test_classroom_output_summary_contains_only_team_scores_and_not_other_team_plans(tmp_path) -> None:
    state_path = tmp_path / "classroom-state.json"
    rounds = (
        ClassroomRoundInput(
            round_key="2027_jan_jun",
            submissions=tmp_path / "round-1",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        ),
        ClassroomRoundInput(
            round_key="2027_jul_dec",
            submissions=tmp_path / "round-2",
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        ),
    )

    result = evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((_plan("team-a"), _plan("team-b"))),
        evaluator=lambda *args, **kwargs: _UsableEvaluation(),
    )

    payload = result.to_public_dict()
    assert payload["submissions"][0]["team_results"]["team_count"] == 1
    assert "plans" not in payload["submissions"][0]
    serialized = str(payload)
    assert "team-a" not in serialized
    assert "submission_key" not in serialized
    assert "week-1" in payload["weekly_rankings"]


def test_scored_week_includes_submission_one_capex_when_submission_two_omits_assets(
    tmp_path,
) -> None:
    state_path = tmp_path / "classroom-state.json"
    first_plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-a", "b0001", 40.0, 10.0, 5.0, 20.0),),
    )
    second_plan = TeamPlan(team_id="team-a", sites=())
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / label,
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for round_key, label in (
            ("2027_jan_jun", "week-1-practice"),
            ("2027_jul_dec", "week-1-scored"),
            ("2028_jan_jun", "practice"),
            ("2028_jul_dec", "scored"),
        )
    )
    observed: list[dict[str, object]] = []

    def loader(submissions, **kwargs):
        return (
            first_plan
            if Path(submissions).name == "practice"
            else second_plan
            if Path(submissions).name == "scored"
            else TeamPlan("team-a", sites=()),
        )

    def evaluator(*args, **kwargs):
        observed.append(kwargs)
        scope = kwargs["capital_cost_scope"]
        materialized = kwargs["plan_overrides"]["team-a"]
        generator_cost = sum(
            generator.capacity_mw
            for generator in materialized.generators
            if generator.generator_id in scope.generator_ids_for("team-a")
        )
        storage_cost = sum(
            storage.power_mw + storage.energy_mwh
            for storage in materialized.storages
            if storage.storage_id in scope.storage_ids_for("team-a")
        )
        site_cost = kwargs["site_cost_connection_capacity_mw_by_team"]["team-a"]
        return _UsableEvaluation(
            team_scores=(
                TeamScore(
                    team_id="team-a",
                    rank=1,
                    valid=True,
                    feasible=True,
                    score=100.0 - generator_cost - storage_cost - site_cost,
                    served_load_mwh=1.0,
                    it_value=100.0,
                    energy_cost=0.0,
                    generator_operating_cost=0.0,
                    generator_capital_cost=generator_cost,
                    site_cost=site_cost,
                    storage_capital_cost=storage_cost,
                    competition_scope="joint",
                ),
            ),
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={
                            "team-a": {
                                storage.storage_id: storage.energy_mwh * 0.75
                                for storage in materialized.storages
                            }
                        }
                    ),
                )
            ),
        )

    result = evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=loader,
        evaluator=evaluator,
    )

    assert observed[2]["capital_cost_scope"] == CapitalCostScope(
        generator_ids_by_team={"team-a": {"generator:team-a:site-a"}},
        storage_ids_by_team={"team-a": {"storage:team-a:site-a"}},
    )
    assert observed[2]["site_cost_connection_capacity_mw_by_team"] == {"team-a": 40.0}
    assert observed[3]["capital_cost_scope"] == observed[2]["capital_cost_scope"]
    assert observed[3]["site_cost_connection_capacity_mw_by_team"] == {"team-a": 40.0}
    scored = result.weekly_rankings[1].rankings[0]
    assert scored.generator_capital_cost == 10.0
    assert scored.storage_capital_cost == 25.0
    assert scored.site_cost == 40.0
    assert scored.score == 25.0


def test_scored_week_does_not_repeat_capex_for_reused_submission_two_site(
    tmp_path,
) -> None:
    state_path = tmp_path / "classroom-state.json"
    plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-a", "b0001", 40.0, 10.0, 5.0, 20.0),),
    )
    rounds = tuple(
        ClassroomRoundInput(
            round_key=round_key,
            submissions=tmp_path / label,
            artifact_dir=tmp_path / "artifacts",
            horizon=1,
            rules=object(),
        )
        for round_key, label in (
            ("2027_jan_jun", "week-1-practice"),
            ("2027_jul_dec", "week-1-scored"),
            ("2028_jan_jun", "practice"),
            ("2028_jul_dec", "scored"),
        )
    )
    observed: list[dict[str, object]] = []

    def evaluator(*args, **kwargs):
        observed.append(kwargs)
        return _UsableEvaluation(
            team_scores=(
                TeamScore(
                    team_id="team-a",
                    rank=1,
                    valid=True,
                    feasible=True,
                    score=25.0,
                    served_load_mwh=1.0,
                    it_value=100.0,
                    energy_cost=0.0,
                    generator_operating_cost=0.0,
                    generator_capital_cost=10.0,
                    site_cost=40.0,
                    storage_capital_cost=25.0,
                    competition_scope="joint",
                ),
            ),
            dispatch=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        storage_soc_mwh_by_team={"team-a": {"storage:team-a:site-a": 15.0}}
                    ),
                )
            ),
        )

    evaluate_classroom(
        state_path=state_path,
        rounds=rounds,
        plan_loader=_loader((plan,)),
        evaluator=evaluator,
    )

    assert observed[1]["capital_cost_scope"] == observed[0]["capital_cost_scope"]
    assert observed[1]["site_cost_connection_capacity_mw_by_team"] == {"team-a": 40.0}
    assert observed[1]["site_cost_connection_count_by_team"] == {"team-a": 1}
