"""Transactional coordination of one evaluated multi-year game round."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .batch_evaluation import (
    BatchEvaluation,
    BatchScoringRules,
    _collect_evaluation_inputs,
    evaluate_csv_batch,
)
from .csv_submission import load_bus_ids
from .game_state import GameState
from .game_state_store import GameStateStore
from .models import TeamPlan


class RoundCoordinatorError(RuntimeError):
    """Raised when a round cannot be prepared or committed."""


PlanLoader = Callable[..., Sequence[TeamPlan]]
Evaluator = Callable[..., BatchEvaluation]


def _default_plan_loader(
    submissions: Sequence[str | Path] | str | Path,
    *,
    artifact_dir: str | Path,
    horizon: int,
    rules: BatchScoringRules,
) -> tuple[TeamPlan, ...]:
    """Reuse the evaluator's strict CSV collection path for state updates."""
    try:
        bus_ids = load_bus_ids(artifact_dir)
        collection = _collect_evaluation_inputs(
            submissions,
            bus_ids=bus_ids,
            horizon=horizon,
            default_generator_cost_per_mwh=rules.default_generator_cost_per_mwh,
            require_complete_horizon=True,
        )
    except (OSError, ValueError) as exc:
        raise RoundCoordinatorError(f"could not validate round submissions: {exc}") from exc
    if collection.rejected:
        reasons = "; ".join(
            f"{record.team_id}: {record.failure_reason or 'submission rejected'}"
            for record in collection.rejected
        )
        raise RoundCoordinatorError(f"rejected submissions: {reasons}")
    return tuple(
        record.submission.plan
        for record in collection.accepted
        if record.submission is not None
    )


def evaluate_round(
    *,
    state_path: str | Path,
    round_key: str,
    submissions: Sequence[str | Path] | str | Path,
    artifact_dir: str | Path,
    horizon: int,
    rules: BatchScoringRules,
    plan_loader: PlanLoader | None = None,
    evaluator: Evaluator = evaluate_csv_batch,
    evaluator_kwargs: Mapping[str, Any] | None = None,
) -> "RoundEvaluationResult":
    """Evaluate one round and commit its state only after success.

    The existing half-year evaluator remains responsible for dispatch and
    scoring. This wrapper owns only the transactional sequence: load prior
    state, validate/register current plans, evaluate, then save the advanced
    state. Any exception before the final save leaves the previous file
    untouched.
    """
    store = GameStateStore(state_path)
    try:
        prior_state = store.load() if Path(state_path).exists() else GameState.new()
    except FileNotFoundError:
        prior_state = GameState.new()

    loader = plan_loader or _default_plan_loader
    plans = tuple(
        loader(
            submissions,
            artifact_dir=artifact_dir,
            horizon=horizon,
            rules=rules,
        )
    )
    advanced_state = prior_state.advance(round_key, plans)

    kwargs = dict(evaluator_kwargs or {})
    evaluation = evaluator(
        submissions,
        artifact_dir=artifact_dir,
        horizon=horizon,
        rules=rules,
        **kwargs,
    )
    rejected = getattr(evaluation, "rejected_submissions", ())
    if rejected:
        raise RoundCoordinatorError(
            "evaluation returned rejected submissions; state was not committed"
        )
    if getattr(evaluation, "dispatch", None) is None:
        raise RoundCoordinatorError(
            "evaluation did not return a usable dispatch result; state was not committed"
        )
    store.save(advanced_state)
    return RoundEvaluationResult(
        round_key=round_key,
        evaluation=evaluation,
        prior_state=prior_state,
        state=advanced_state,
    )


class RoundEvaluationResult:
    """Result of a committed round, including before/after game state."""

    def __init__(
        self,
        *,
        round_key: str,
        evaluation: BatchEvaluation,
        prior_state: GameState,
        state: GameState,
    ) -> None:
        self.round_key = round_key
        self.evaluation = evaluation
        self.prior_state = prior_state
        self.state = state
