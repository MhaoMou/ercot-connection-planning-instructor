"""Transactional coordination of one evaluated multi-year game round."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import inspect
import math
from pathlib import Path
from typing import Any

from .batch_evaluation import (
    BatchEvaluation,
    BatchScoringRules,
    CapitalCostScope,
    TeamScore,
    _collect_evaluation_inputs,
    evaluate_csv_batch,
)
from .asset_state import (
    AssetStateError,
    canonical_asset_key,
    canonical_asset_keys_for_plan,
)
from .calendars import weekly_match_for
from .csv_submission import load_bus_ids
from .game_state import RELEASED_ROUND_SEQUENCE, GameState
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
    "site_cost_connection_capacity_mw_by_team",
    "submission_number",
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


def _state_for_round(state: GameState, round_key: str) -> GameState:
    """Apply the weekly reset before plans are materialized or evaluated."""
    match = weekly_match_for(round_key)
    if state.current_week_id == match.week_id:
        return state
    return replace(state, asset_ledger=state.asset_ledger.reset())


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
        loaded_state = store.load() if Path(state_path).exists() else GameState.new()
    except FileNotFoundError:
        loaded_state = GameState.new()

    try:
        match = weekly_match_for(round_key)
    except ValueError as exc:
        raise RoundCoordinatorError(str(exc)) from exc
    expected_round = loaded_state.next_round_key
    if expected_round != round_key:
        if (
            match.round_keys[1] == round_key
            and loaded_state.current_round_key != match.round_keys[0]
        ):
            raise RoundCoordinatorError(
                f"submission 1 for {match.week_id} must be successfully committed "
                f"before submission 2 ({match.round_keys[0]})"
            )
        raise RoundCoordinatorError(
            f"round transition expected {expected_round}, received {round_key}"
        )

    ledger_reset = (
        loaded_state.current_round_key is not None
        and loaded_state.current_week_id != match.week_id
    )
    preserves_prior_submission = (
        loaded_state.current_week_id == match.week_id
        and match.round_keys[1] == round_key
    )
    prior_state = _state_for_round(loaded_state, round_key)

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
    new_site_capacity_mw: dict[str, float] = {}
    for plan in plans:
        # Materialize before dispatch so custom loaders cannot bypass the
        # Task 1 state/schema boundary with a raw site-only TeamPlan.
        materialized_plans[plan.team_id] = prior_state.asset_ledger.materialize_plan(
            plan,
            horizon=horizon,
        )
        materialized = materialized_plans[plan.team_id]
        prior_team = prior_state.asset_ledger.teams.get(plan.team_id)
        prior_generator_keys = (
            frozenset(asset.asset_key for asset in prior_team.generators.values())
            if prior_team is not None
            else frozenset()
        )
        prior_storage_keys = (
            frozenset(asset.asset_key for asset in prior_team.storages.values())
            if prior_team is not None
            else frozenset()
        )
        prior_site_ids = frozenset(prior_team.sites) if prior_team is not None else frozenset()
        if plan.connections:
            new_site_capacity_mw[plan.team_id] = sum(
                connection.capacity_mw
                for connection in plan.connections
                if connection.connection_id not in prior_site_ids
            )
        else:
            new_site_capacity_mw[plan.team_id] = sum(
                site.connection_capacity_mw
                for site in plan.sites
                if site.site_id not in prior_site_ids
            )
        materialized_generator_ids = {
            canonical_asset_key(plan.team_id, generator.connection_id, "generator"): generator.generator_id
            for generator in materialized.generators
        }
        materialized_storage_ids = {
            canonical_asset_key(plan.team_id, storage.connection_id, "storage"): storage.storage_id
            for storage in materialized.storages
        }
        declared_generator_keys = canonical_asset_keys_for_plan(plan, kind="generator")
        declared_storage_keys = canonical_asset_keys_for_plan(plan, kind="storage")
        new_generators = frozenset(
            materialized_generator_ids[asset_key]
            for asset_key in declared_generator_keys
            if asset_key not in prior_generator_keys
        )
        new_storages = frozenset(
            materialized_storage_ids[asset_key]
            for asset_key in declared_storage_keys
            if asset_key not in prior_storage_keys
        )
        if new_generators:
            new_generator_ids[plan.team_id] = new_generators
        if new_storages:
            new_storage_ids[plan.team_id] = new_storages

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
            plan.team_id: len(plan.connections) if plan.connections else len(plan.sites)
            for plan in plans
        },
        "site_cost_connection_capacity_mw_by_team": new_site_capacity_mw,
        "submission_number": weekly_match_for(round_key).round_keys.index(round_key) + 1,
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
    _validate_projection_team_isolation(evaluation, materialized_plans)
    updated_state = _record_final_storage_soc(advanced_state, materialized_plans, evaluation)
    store.save(updated_state)
    return RoundEvaluationResult(
        round_key=round_key,
        evaluation=evaluation,
        prior_state=prior_state,
        state=updated_state,
        ledger_reset=ledger_reset,
        preserves_prior_submission=preserves_prior_submission,
    )


@dataclass(frozen=True)
class ClassroomRoundInput:
    """Inputs for one half-year in the released classroom schedule."""

    round_key: str
    submissions: Sequence[str | Path] | str | Path
    artifact_dir: str | Path
    horizon: int
    rules: BatchScoringRules


@dataclass(frozen=True)
class WeeklyRanking:
    """Practice feedback and the scored ranking for one weekly match."""

    week_id: str
    feedback_only: bool
    rankings: tuple[TeamScore, ...] = ()
    practice_feedback: tuple[TeamScore, ...] = ()


@dataclass(frozen=True)
class ClassroomEvaluationResult:
    """Public result for a sequential classroom evaluation."""

    submissions: tuple["RoundEvaluationResult", ...]
    weekly_rankings: tuple[WeeklyRanking, ...]
    final_rankings: tuple[TeamScore, ...]
    state: GameState

    def to_public_dict(self) -> dict[str, Any]:
        """Return score-only output safe to publish to classroom consumers."""
        return {
            "submissions": [submission.to_public_dict() for submission in self.submissions],
            "weekly_rankings": {
                ranking.week_id: {
                    "feedback_only": ranking.feedback_only,
                    "practice_feedback": [
                        _public_score_dict(score) for score in ranking.practice_feedback
                    ],
                    "rankings": [_public_score_dict(score) for score in ranking.rankings],
                }
                for ranking in self.weekly_rankings
            },
            "final_rankings": [_public_score_dict(score) for score in self.final_rankings],
            "state_metadata": {
                "current_round_key": self.state.current_round_key,
                "next_round_key": self.state.next_round_key,
                "completed_rounds": list(self.state.completed_rounds),
            },
        }


def evaluate_classroom(
    *,
    state_path: str | Path,
    rounds: Sequence[ClassroomRoundInput],
    plan_loader: PlanLoader | None = None,
    evaluator: Evaluator = evaluate_csv_batch,
    evaluator_kwargs: Mapping[str, Any] | None = None,
) -> ClassroomEvaluationResult:
    """Evaluate a contiguous classroom schedule prefix transactionally.

    A caller may supply all six rounds or a prefix when resuming an interrupted
    classroom run.  Each round is committed through :func:`evaluate_round`,
    so a failed round cannot advance the state or expose a partial ranking.
    """
    round_inputs = tuple(rounds)
    if not round_inputs or len(round_inputs) > len(RELEASED_ROUND_SEQUENCE):
        raise RoundCoordinatorError("classroom rounds must contain a non-empty released schedule prefix")
    expected_rounds = RELEASED_ROUND_SEQUENCE[: len(round_inputs)]
    actual_rounds = tuple(item.round_key for item in round_inputs)
    if actual_rounds != expected_rounds:
        raise RoundCoordinatorError(
            "classroom rounds must be a contiguous prefix of the released six-round schedule"
        )

    results: list[RoundEvaluationResult] = []
    for item in round_inputs:
        results.append(
            evaluate_round(
                state_path=state_path,
                round_key=item.round_key,
                submissions=item.submissions,
                artifact_dir=item.artifact_dir,
                horizon=item.horizon,
                rules=item.rules,
                plan_loader=plan_loader,
                evaluator=evaluator,
                evaluator_kwargs=evaluator_kwargs,
            )
        )

    weekly_rankings: list[WeeklyRanking] = []
    final_scores: list[TeamScore] = []
    for week_id, week_results in _group_week_results(results):
        first = week_results[0]
        practice_feedback = tuple(getattr(first.evaluation, "team_scores", ()))
        if first.feedback_only:
            rankings: tuple[TeamScore, ...] = ()
        elif len(week_results) < 2:
            rankings = ()
        else:
            rankings = _rank_scores(getattr(week_results[1].evaluation, "team_scores", ()))
            final_scores.extend(score for score in rankings if score.valid)
        weekly_rankings.append(
            WeeklyRanking(
                week_id=week_id,
                feedback_only=first.feedback_only,
                rankings=rankings,
                practice_feedback=practice_feedback,
            )
        )

    aggregate_by_team: dict[str, TeamScore] = {}
    for score in final_scores:
        previous = aggregate_by_team.get(score.team_id)
        if previous is None:
            aggregate_by_team[score.team_id] = score
        else:
            aggregate_by_team[score.team_id] = replace(
                previous,
                score=previous.score + score.score,
                served_load_mwh=previous.served_load_mwh + score.served_load_mwh,
                it_value=previous.it_value + score.it_value,
                energy_cost=previous.energy_cost + score.energy_cost,
                generator_operating_cost=(
                    previous.generator_operating_cost + score.generator_operating_cost
                ),
                generator_capital_cost=(
                    previous.generator_capital_cost + score.generator_capital_cost
                ),
                site_cost=previous.site_cost + score.site_cost,
                storage_capital_cost=(
                    previous.storage_capital_cost + score.storage_capital_cost
                ),
            )
    final_rankings = _rank_scores(tuple(aggregate_by_team.values()))
    return ClassroomEvaluationResult(
        submissions=tuple(results),
        weekly_rankings=tuple(weekly_rankings),
        final_rankings=final_rankings,
        state=results[-1].state,
    )


def _group_week_results(
    results: Sequence["RoundEvaluationResult"],
) -> tuple[tuple[str, tuple["RoundEvaluationResult", ...]], ...]:
    grouped: dict[str, list[RoundEvaluationResult]] = {}
    for result in results:
        grouped.setdefault(result.week_id, []).append(result)
    return tuple((week_id, tuple(grouped[week_id])) for week_id in grouped)


def _rank_scores(scores: Sequence[TeamScore]) -> tuple[TeamScore, ...]:
    ordered = sorted(
        scores,
        key=lambda item: (
            0 if item.valid and item.feasible else 1 if item.valid else 2,
            -item.score,
            item.team_id,
        ),
    )
    return tuple(replace(score, rank=index) for index, score in enumerate(ordered, start=1))


def _public_score_dict(score: Any) -> dict[str, Any]:
    payload = asdict(score)
    payload.pop("submission_key", None)
    return payload


def _validate_projection_team_isolation(
    evaluation: Any,
    materialized_plans: Mapping[str, TeamPlan],
) -> None:
    """Reject projection diagnostics that leak rejected or unknown teams."""
    diagnostics = getattr(evaluation, "projection_diagnostics", None)
    if diagnostics is None:
        return
    valid_team_ids = set(materialized_plans)
    for field_name in (
        "service_fraction_by_team",
        "projected_capacity_mw_by_team",
        "capped_request_capacity_mw_by_team",
        "requested_load_mwh_by_team",
        "projected_load_mwh_by_team",
    ):
        values = getattr(diagnostics, field_name, None)
        if not isinstance(values, Mapping):
            raise RoundCoordinatorError(
                f"projection diagnostics field {field_name} must be a mapping; state was not committed"
            )
        unknown_team_ids = sorted(set(values) - valid_team_ids)
        if unknown_team_ids:
            raise RoundCoordinatorError(
                "projection diagnostics contain rejected or unknown team(s): "
                + ", ".join(unknown_team_ids)
                + "; state was not committed"
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


def _record_final_storage_soc(
    state: GameState,
    materialized_plans: Mapping[str, TeamPlan],
    evaluation: Any,
) -> GameState:
    """Validate and persist final SOC only after a usable round evaluation."""
    storages_by_team = {
        team_id: tuple(plan.storages)
        for team_id, plan in materialized_plans.items()
        if plan.storages
    }
    if not storages_by_team:
        return state

    dispatch = evaluation.dispatch
    try:
        dispatched_team_ids = frozenset(dispatch.team_ids)
    except AttributeError:
        # Lightweight evaluator doubles from earlier stages do not expose the
        # BatchDispatchResult team set, so retain the original strict behavior.
        expected_storages_by_team = storages_by_team
    else:
        expected_storages_by_team = {
            team_id: storages
            for team_id, storages in storages_by_team.items()
            if team_id in dispatched_team_ids
        }

    if not expected_storages_by_team:
        return state

    periods = dispatch.periods
    final_period = periods[-1]
    reported = getattr(final_period, "storage_soc_mwh_by_team", None)
    if not isinstance(reported, Mapping):
        raise RoundCoordinatorError(
            "missing final storage state of charge; state was not committed"
        )

    soc_by_team: dict[str, dict[str, float]] = {}
    for team_id, storages in expected_storages_by_team.items():
        values = reported.get(team_id)
        if not isinstance(values, Mapping):
            raise RoundCoordinatorError(
                f"missing final storage state of charge for team {team_id}; state was not committed"
            )
        team_soc: dict[str, float] = {}
        for storage in storages:
            if storage.storage_id not in values:
                raise RoundCoordinatorError(
                    f"missing final storage state of charge for {team_id}/{storage.storage_id}; "
                    "state was not committed"
                )
            value = values[storage.storage_id]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RoundCoordinatorError(
                    f"malformed final storage state of charge for {team_id}/{storage.storage_id}; "
                    "state was not committed"
                )
            if value < storage.energy_mwh * 0.5:
                raise RoundCoordinatorError(
                    f"final storage state of charge for {team_id}/{storage.storage_id} "
                    "must be at least half of energy capacity; state was not committed"
                )
            if value > storage.energy_mwh:
                raise RoundCoordinatorError(
                    f"final storage state of charge for {team_id}/{storage.storage_id} "
                    "exceeds energy capacity; state was not committed"
                )
            team_soc[storage.storage_id] = float(value)
        soc_by_team[team_id] = team_soc

    try:
        ledger = state.asset_ledger.update_storage_soc(soc_by_team)
    except AssetStateError as exc:
        raise RoundCoordinatorError(
            f"could not persist final storage state of charge; state was not committed: {exc}"
        ) from exc
    return replace(state, asset_ledger=ledger)


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
        ledger_reset: bool = False,
        preserves_prior_submission: bool = False,
    ) -> None:
        self.round_key = round_key
        self.evaluation = evaluation
        self.prior_state = prior_state
        self.state = state
        self.ledger_reset = ledger_reset
        self.preserves_prior_submission = preserves_prior_submission

    @property
    def week_id(self) -> str:
        return weekly_match_for(self.round_key).week_id

    @property
    def submission_number(self) -> int:
        match = weekly_match_for(self.round_key)
        return match.round_keys.index(self.round_key) + 1

    @property
    def feedback_only(self) -> bool:
        return weekly_match_for(self.round_key).feedback_only

    @property
    def scored(self) -> bool:
        return not self.feedback_only and self.submission_number == 2

    @property
    def predecessor_round_key(self) -> str | None:
        return (
            weekly_match_for(self.round_key).round_keys[0]
            if self.submission_number == 2
            else None
        )

    @property
    def team_results(self) -> tuple[TeamScore, ...]:
        return tuple(getattr(self.evaluation, "team_scores", ()))

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize metadata and score feedback without exposing submitted plans."""
        return {
            "round_key": self.round_key,
            "week_id": self.week_id,
            "submission_number": self.submission_number,
            "feedback_only": self.feedback_only,
            "scored": self.scored,
            "ledger_reset": self.ledger_reset,
            "preserves_prior_submission": self.preserves_prior_submission,
            "predecessor_round_key": self.predecessor_round_key,
            "team_results": [_public_score_dict(score) for score in self.team_results],
        }
