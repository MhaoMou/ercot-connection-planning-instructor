from __future__ import annotations

import json

import pytest

from ercot_game.asset_state import AssetStateError
from ercot_game.game_state import GameState, RoundTransitionError
from ercot_game.models import ConnectionPlan, GeneratorPlan, TeamPlan


def _plan(team_id: str = "team-a") -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(
            ConnectionPlan("site-1", "b0001", 40.0, (1.0,)),
        ),
        generators=(
            GeneratorPlan("gen-1", "site-1", 10.0, 16.0),
        ),
    )


def test_new_game_state_exposes_first_round() -> None:
    state = GameState.new()

    assert state.current_round_key is None
    assert state.next_round_key == "2027_jan_jun"


def test_advance_persists_assets_and_moves_to_next_half_year() -> None:
    state = GameState.new().advance("2027_jan_jun", (_plan(),))

    assert state.current_round_key == "2027_jan_jun"
    assert state.next_round_key == "2027_jul_dec"
    assert state.asset_ledger.team("team-a").generators["gen-1"].bus_id == "b0001"

    next_state = state.advance("2027_jul_dec", (TeamPlan(team_id="team-a", connections=()),))
    assert next_state.current_round_key == "2027_jul_dec"
    assert next_state.asset_ledger.team("team-a").generators["gen-1"].capacity_mw == 10.0


def test_rounds_must_be_completed_in_order() -> None:
    state = GameState.new()

    with pytest.raises(RoundTransitionError, match="expected 2027_jan_jun"):
        state.advance("2028_jan_jun", ())

    state = state.advance("2027_jan_jun", ())
    with pytest.raises(RoundTransitionError, match="expected 2027_jul_dec"):
        state.advance("2027_jan_jun", ())


def test_duplicate_team_plans_are_rejected_before_asset_updates() -> None:
    state = GameState.new()

    with pytest.raises(RoundTransitionError, match="at most one plan"):
        state.advance("2027_jan_jun", (_plan("team-a"), _plan("team-a")))

    assert state.current_round_key is None
    assert state.asset_ledger.team_ids() == ()


def test_state_round_trip_is_json_safe() -> None:
    state = GameState.new().advance("2027_jan_jun", (_plan(),))
    restored = GameState.from_dict(json.loads(json.dumps(state.to_dict())))

    assert restored.to_dict() == state.to_dict()


def test_invalid_round_key_is_rejected() -> None:
    with pytest.raises(RoundTransitionError, match="released half-year"):
        GameState.new().advance("2018_jan_jun", ())


def test_asset_conflict_does_not_mutate_prior_state() -> None:
    state = GameState.new().advance("2027_jan_jun", (_plan(),))
    conflicting = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-2", "b0002", 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-1", "site-2", 10.0, 16.0),),
    )

    with pytest.raises(AssetStateError):
        state.advance("2027_jul_dec", (conflicting,))

    assert state.current_round_key == "2027_jan_jun"
    assert state.next_round_key == "2027_jul_dec"
