"""Transactional coordination of one evaluated multi-year game round."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
from pathlib import Path
from typing import Any

from .batch_evaluation import (
    BatchEvaluation,
    BatchScoringRules,
    CapitalCostScope,
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
_STAGE6_EVALUATOR_KWARGS = (
    "plan_overrides",
    "capital_cost_scope",
    "site_cost_connection_count_by_team",
)


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
    # Keep only accepted plans.  The batch evaluator deliberately isolates
    # malformed submissions so valid teams can still be evaluated; rejected
    # records must not enter the persistent asset ledger.
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
    materialized_plans: dict[str, TeamPlan] = {}
    new_generator_ids: dict[str, frozenset[str]] = {}
    new_storage_ids: dict[str, frozenset[str]] = {}
    for plan in plans:
        materialized_plans[plan.team_id] = prior_state.asset_ledger.materialize_plan(
            plan,
            horizon=horizon,
        )
        prior_team = prior_state.asset_ledger.teams.get(plan.team_id)
        prior_generator_ids = frozenset(prior_team.generators) if prior_team is not None else frozenset()
        prior_storage_ids = frozenset(prior_team.storages) if prior_team is not None else frozenset()
        new_generator_ids[plan.team_id] = frozenset(
            generator.generator_id for generator in plan.generators
        ) - prior_generator_ids
        new_storage_ids[plan.team_id] = frozenset(
            storage.storage_id for storage in plan.storages
        ) - prior_storage_ids

    kwargs = dict(evaluator_kwargs or {})
    if any(name in kwargs for name in _STAGE6_EVALUATOR_KWARGS):
        raise RoundCoordinatorError(
            "evaluate_round owns the Stage 6 integration kwargs"
        )
    optional_kwargs = {
        "plan_overrides": materialized_plans,
        "capital_cost_scope": CapitalCostScope(
            generator_ids_by_team=new_generator_ids,
            storage_ids_by_team=new_storage_ids,
        ),
        "site_cost_connection_count_by_team": {
            plan.team_id: len(plan.connections)
            for plan in plans
        },
    }
    stage6_kwargs = _require_stage6_evaluator(evaluator, optional_kwargs)
    advanced_state = prior_state.advance(round_key, plans)
    kwargs.update(stage6_kwargs)
    evaluation = evaluator(
        submissions,
        artifact_dir=artifact_dir,
        horizon=horizon,
        rules=rules,
        **kwargs,
    )
    # Rejected files are isolated by the existing batch evaluator.  They must
    # not prevent valid teams from receiving a result or having their accepted
    # assets committed; only the accepted plans returned by the plan loader
    # enter the ledger.
    if not _has_period_collection(evaluation):
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


def _has_period_collection(evaluation: Any) -> bool:
    """Accept real and lightweight dispatch objects with a period collection."""
    dispatch = getattr(evaluation, "dispatch", None)
    if dispatch is None:
        return False
    try:
        periods = dispatch.periods
    except AttributeError:
        return False
    if periods is None or isinstance(periods, (str, bytes)):
        return False
    try:
        period_count = len(periods)
    except (TypeError, ValueError):
        return False
    return period_count > 0


def _require_stage6_evaluator(
    evaluator: Evaluator,
    stage6_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    """Require an evaluator that can receive every Stage 6 integration input."""
    try:
        signature = inspect.signature(evaluator)
    except (TypeError, ValueError):
        raise RoundCoordinatorError(
            "evaluate_round requires a Stage 6-aware evaluator with an inspectable signature"
        ) from None
    parameters = signature.parameters.values()
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return dict(stage6_kwargs)
    unsupported = [
        name
        for name in _STAGE6_EVALUATOR_KWARGS
        if name not in signature.parameters
        or signature.parameters[name].kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    if unsupported:
        raise RoundCoordinatorError(
            "evaluate_round requires a Stage 6-aware evaluator accepting: "
            + ", ".join(_STAGE6_EVALUATOR_KWARGS)
            + "; unsupported: "
            + ", ".join(unsupported)
        )
    return dict(stage6_kwargs)


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
