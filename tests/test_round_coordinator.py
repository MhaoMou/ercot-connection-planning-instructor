from __future__ import annotations

from dataclasses import dataclass

import pytest

from ercot_game.asset_state import AssetStateError
from ercot_game.game_state import GameState
from ercot_game.game_state_store import GameStateStore
from ercot_game.models import ConnectionPlan, GeneratorPlan, TeamPlan
from ercot_game.round_coordinator import RoundCoordinatorError, evaluate_round


def _plan(team_id: str = "team-a", bus_id: str = "b0001") -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(ConnectionPlan("site-1", bus_id, 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-1", "site-1", 10.0, 16.0),),
    )


@dataclass(frozen=True)
class _UsableEvaluation:
    rejected_submissions: tuple = ()
    dispatch: object = object()


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
    rejected = _UsableEvaluation(rejected_submissions=(object(),))

    with pytest.raises(RoundCoordinatorError, match="rejected submissions"):
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
