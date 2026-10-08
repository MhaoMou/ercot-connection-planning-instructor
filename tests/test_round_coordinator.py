from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from ercot_game.asset_state import AssetStateError
from ercot_game.game_state import GameState
from ercot_game.game_state_store import GameStateStore
from ercot_game.batch_evaluation import CapitalCostScope
from ercot_game.models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan
from ercot_game import round_coordinator
from ercot_game.round_coordinator import RoundCoordinatorError, evaluate_round


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
        generator_ids_by_team={"team-a": {"gen-new"}},
        storage_ids_by_team={"team-a": {"store-new"}},
    )
    assert seen["site_cost_connection_count_by_team"] == {"team-a": 1}
    assert set(result.state.asset_ledger.team("team-a").generators) == {
        "gen-new",
        "gen-old",
    }


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
