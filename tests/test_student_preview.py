from dataclasses import replace

import pytest

from ercot_game.batch_evaluation import BatchScoringRules
from ercot_game.models import ConnectionPlan, StoragePlan, TeamPlan
from ercot_game import student_preview
from tools.activsg2000.plan_adapter import BatchDispatchResult


def _plan(team_id: str, *, storage: bool = True) -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(ConnectionPlan("site-1", "B1", 10.0),),
        storages=(StoragePlan("storage-1", "site-1", 2.0, 8.0),) if storage else (),
    )


def _rules() -> BatchScoringRules:
    return BatchScoringRules(
        rules_version="test",
        it_value_per_mwh=(100.0, 100.0),
        energy_price_per_mwh=(10.0, 10.0),
        generator_capital_cost_per_mw=1.0,
        storage_capital_cost_per_mw=2.0,
        storage_capital_cost_per_mwh=3.0,
        site_cost_per_mw=4.0,
    )


def test_preview_samples_full_horizon_and_strips_storage_from_fast_dispatch(monkeypatch):
    observed = {}

    def fake_dispatch(artifact_dir, plans, *, period_indices, workers=1, stop_on_infeasible=False):
        observed["plans"] = tuple(plans)
        observed["period_indices"] = period_indices
        observed["workers"] = workers
        return BatchDispatchResult(team_ids=("team-a",), periods=())

    monkeypatch.setattr(student_preview, "evaluate_plans_with_prepared_dcopf", fake_dispatch)
    result = student_preview.preview_plans(
        [_plan("team-a")],
        artifact_dir="artifact",
        horizon=2,
        rules=_rules(),
        workers=3,
    )

    assert observed["period_indices"] == (0,)
    assert observed["workers"] == 3
    assert observed["plans"][0].storages == ()
    assert result.exact is False
    assert result.storage_dispatch_policy == "idle_for_speed;sampled_every_6_hours"
    assert result.team_scores[0].storage_capital_cost == pytest.approx(28.0)


def test_preview_rejects_invalid_horizon():
    with pytest.raises(ValueError, match="horizon"):
        student_preview.preview_plans(
            [_plan("team-a")],
            artifact_dir="artifact",
            horizon=0,
            rules=_rules(),
        )
