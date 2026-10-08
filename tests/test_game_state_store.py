from __future__ import annotations

import json

import pytest

from ercot_game.game_state_store import GameStateStore, GameStateStoreError
from ercot_game.game_state import GameState
from ercot_game.models import ConnectionPlan, GeneratorPlan, TeamPlan


def _state() -> GameState:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-1", "b0001", 40.0, (1.0,)),),
        generators=(GeneratorPlan("gen-1", "site-1", 10.0, 16.0),),
    )
    return GameState.new().advance("2027_jan_jun", (plan,))


def test_save_load_round_trip(tmp_path) -> None:
    path = tmp_path / "state" / "game_state.json"
    store = GameStateStore(path)

    store.save(_state())

    assert store.load().to_dict() == _state().to_dict()
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_save_output_is_deterministic(tmp_path) -> None:
    path = tmp_path / "game_state.json"
    store = GameStateStore(path)
    state = _state()

    store.save(state)
    first = path.read_bytes()
    store.save(state)

    assert path.read_bytes() == first
    assert json.loads(first)["state_version"] == 1


def test_missing_file_is_reported_as_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        GameStateStore(tmp_path / "missing.json").load()


def test_malformed_json_has_domain_error(tmp_path) -> None:
    path = tmp_path / "game_state.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(GameStateStoreError, match="malformed JSON"):
        GameStateStore(path).load()


def test_unsupported_state_has_domain_error(tmp_path) -> None:
    path = tmp_path / "game_state.json"
    path.write_text(json.dumps({"state_version": 99}), encoding="utf-8")

    with pytest.raises(GameStateStoreError, match="invalid game state"):
        GameStateStore(path).load()


def test_save_replaces_target_atomically(tmp_path, monkeypatch) -> None:
    import os

    path = tmp_path / "game_state.json"
    path.write_text("old", encoding="utf-8")
    observed: dict[str, str] = {}
    real_replace = os.replace

    def replace(source, target):
        observed["source"] = str(source)
        observed["target"] = str(target)
        real_replace(source, target)

    monkeypatch.setattr("ercot_game.game_state_store.os.replace", replace)
    GameStateStore(path).save(_state())

    assert observed["target"] == str(path)
    assert observed["source"] != str(path)
    assert path.read_text(encoding="utf-8").startswith("{")
    assert not list(tmp_path.glob(".game_state.json.*.tmp"))
