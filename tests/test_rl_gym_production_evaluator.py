from __future__ import annotations

from types import SimpleNamespace

import pytest

from ercot_game.models import SitePlan
from rl_gym import RLPlanState
from rl_gym.production_evaluator import (
    ProductionEvaluatorConfig,
    make_production_evaluator,
)


def make_state() -> RLPlanState:
    return RLPlanState.for_half_year(
        team_id="team-a",
        sites=(SitePlan("site-a", "1001", 10.0, 2.0, 0.0, 0.0),),
        year=2028,
        round_name="jan_jun",
    )


def test_production_callback_routes_to_existing_evaluator(monkeypatch, tmp_path):
    state = make_state()
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules_version":"test-v1"}', encoding="utf-8")
    captured = {}
    rules = object()

    def fake_load(path, *, artifact_dir, horizon):
        captured["load"] = (path, artifact_dir, horizon)
        return rules

    def fake_evaluate(state_arg, *, submission_path, artifact_dir, rules, **kwargs):
        captured["evaluate"] = (state_arg, submission_path, artifact_dir, rules, kwargs)
        state_arg.write_csv(submission_path)
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=42.0),))

    monkeypatch.setattr("rl_gym.production_evaluator.load_scoring_rules", fake_load)
    monkeypatch.setattr("rl_gym.production_evaluator.evaluate_plan_state", fake_evaluate)

    config = ProductionEvaluatorConfig(
        scenario_year=2028,
        scenario_block="jan_jun",
        artifact_dir=tmp_path / "artifact",
        rules_path=rules_path,
        submission_dir=tmp_path / "submissions",
    )
    callback = make_production_evaluator(config)
    result = callback(state, None)

    assert result.team_scores[0].score == 42.0
    assert captured["load"] == (rules_path, config.artifact_dir, 4368)
    evaluated_state, submission_path, artifact_dir, loaded_rules, kwargs = captured["evaluate"]
    assert evaluated_state == state
    assert submission_path.parent == config.submission_dir
    assert submission_path.suffix == ".csv"
    assert artifact_dir == config.artifact_dir
    assert loaded_rules is rules
    assert kwargs == {
        "dispatch_workers": 1,
        "projection_iterations": 10,
        "storage_solver": "benders",
        "storage_solver_backend": "auto",
        "storage_parallel_workers": None,
        "storage_solver_threads": 1,
        "storage_wall_time_limit_seconds": 1500.0,
        "storage_max_iterations": 200,
        "storage_tighten_feasible_bounds": False,
    }
    assert submission_path.read_text(encoding="utf-8").splitlines()[0].startswith("team_id,")


def test_production_callback_rejects_wrong_scenario(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules_version":"test-v1"}', encoding="utf-8")
    monkeypatch.setattr("rl_gym.production_evaluator.load_scoring_rules", lambda *args, **kwargs: object())
    callback = make_production_evaluator(
        ProductionEvaluatorConfig(2028, "jan_jun", tmp_path, rules_path)
    )
    wrong_state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=(SitePlan("site-a", "1001", 10.0, 2.0, 0.0, 0.0),),
        year=2029,
        round_name="jan_jun",
    )

    with pytest.raises(ValueError, match="scenario does not match"):
        callback(wrong_state, None)


def test_production_callback_can_select_explicit_diagnostic_periods(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules_version":"test-v1"}', encoding="utf-8")
    calls = []
    monkeypatch.setattr("rl_gym.production_evaluator.load_scoring_rules", lambda *args, **kwargs: object())

    def fake_evaluate(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=1.0),))

    monkeypatch.setattr("rl_gym.production_evaluator.evaluate_plan_state", fake_evaluate)
    callback = make_production_evaluator(
        ProductionEvaluatorConfig(
                2028,
                "jan_jun",
                tmp_path,
                rules_path,
                submission_dir=tmp_path / "submissions",
                period_indices=(0, 1),
            )
    )
    callback(make_state(), None)

    assert calls[0]["period_indices"] == (0, 1)


def test_production_callback_forwards_storage_solver_controls(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules_version":"test-v1"}', encoding="utf-8")
    captured = {}
    monkeypatch.setattr(
        "rl_gym.production_evaluator.load_scoring_rules",
        lambda *args, **kwargs: object(),
    )

    def fake_evaluate(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=1.0),))

    monkeypatch.setattr("rl_gym.production_evaluator.evaluate_plan_state", fake_evaluate)
    callback = make_production_evaluator(
        ProductionEvaluatorConfig(
            2028,
            "jan_jun",
            tmp_path,
            rules_path,
            submission_dir=tmp_path / "submissions",
            storage_solver="monolithic",
            storage_parallel_workers=2,
            storage_solver_threads=2,
            storage_wall_time_limit_seconds=3600.0,
            storage_max_iterations=25,
            storage_tighten_feasible_bounds=True,
        )
    )
    callback(make_state(), None)

    assert captured["storage_solver"] == "monolithic"
    assert captured["storage_parallel_workers"] == 2
    assert captured["storage_solver_threads"] == 2
    assert captured["storage_wall_time_limit_seconds"] == 3600.0
    assert captured["storage_max_iterations"] == 25
    assert captured["storage_tighten_feasible_bounds"] is True


def test_production_config_rejects_invalid_solver_controls(tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules_version":"test-v1"}', encoding="utf-8")

    with pytest.raises(ValueError, match="dispatch_workers"):
        ProductionEvaluatorConfig(2028, "jan_jun", tmp_path, rules_path, dispatch_workers=0)
    with pytest.raises(ValueError, match="projection_iterations"):
        ProductionEvaluatorConfig(2028, "jan_jun", tmp_path, rules_path, projection_iterations=0)
