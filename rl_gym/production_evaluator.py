"""Production evaluator callback for RL terminal evaluation.

This module is intentionally a thin boundary.  It serializes an immutable
``RLPlanState`` through the canonical CSV contract and delegates validation,
dispatch, projection, and scoring to ``ercot_game.batch_evaluation``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable
import uuid

from ercot_game.batch_evaluation import load_scoring_rules
from ercot_game.calendars import half_year_calendar

from .adapter import RLPlanState, evaluate_plan_state
from .release import ReleaseManifest


@dataclass(frozen=True)
class ProductionEvaluatorConfig:
    """Immutable configuration for the instructor-side evaluator callback."""

    scenario_year: int
    scenario_block: str
    artifact_dir: Path
    rules_path: Path
    submission_dir: Path = Path("work/rl-gym-evaluations")
    dispatch_workers: int = 1
    projection_iterations: int = 10
    period_indices: tuple[int, ...] | None = None
    storage_solver: str = "benders"
    storage_solver_backend: str = "auto"
    storage_parallel_workers: int | None = None
    storage_solver_threads: int = 1
    storage_wall_time_limit_seconds: float | None = 1500.0
    storage_max_iterations: int = 200
    storage_tighten_feasible_bounds: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_year, int) or isinstance(self.scenario_year, bool):
            raise ValueError("scenario_year must be a non-bool integer")
        calendar = half_year_calendar(self.scenario_year, self.scenario_block)
        del calendar
        for field in ("artifact_dir", "rules_path", "submission_dir"):
            value = getattr(self, field)
            if not isinstance(value, Path):
                object.__setattr__(self, field, Path(value))
        if (
            not isinstance(self.dispatch_workers, int)
            or isinstance(self.dispatch_workers, bool)
            or self.dispatch_workers <= 0
        ):
            raise ValueError("dispatch_workers must be a positive integer")
        if (
            not isinstance(self.projection_iterations, int)
            or isinstance(self.projection_iterations, bool)
            or self.projection_iterations <= 0
        ):
            raise ValueError("projection_iterations must be a positive integer")
        if self.storage_solver not in {"benders", "monolithic"}:
            raise ValueError("storage_solver must be 'benders' or 'monolithic'")
        if self.storage_solver_backend not in {"auto", "highs", "gurobi"}:
            raise ValueError("storage_solver_backend must be 'auto', 'highs', or 'gurobi'")
        if self.storage_parallel_workers is not None and (
            not isinstance(self.storage_parallel_workers, int)
            or isinstance(self.storage_parallel_workers, bool)
            or self.storage_parallel_workers <= 0
        ):
            raise ValueError("storage_parallel_workers must be a positive integer or None")
        if (
            not isinstance(self.storage_solver_threads, int)
            or isinstance(self.storage_solver_threads, bool)
            or self.storage_solver_threads <= 0
        ):
            raise ValueError("storage_solver_threads must be a positive integer")
        if self.storage_wall_time_limit_seconds is not None and (
            isinstance(self.storage_wall_time_limit_seconds, bool)
            or not isinstance(self.storage_wall_time_limit_seconds, (int, float))
            or not math.isfinite(float(self.storage_wall_time_limit_seconds))
            or self.storage_wall_time_limit_seconds < 0
        ):
            raise ValueError("storage_wall_time_limit_seconds must be finite, non-negative, or None")
        if (
            not isinstance(self.storage_max_iterations, int)
            or isinstance(self.storage_max_iterations, bool)
            or self.storage_max_iterations <= 0
        ):
            raise ValueError("storage_max_iterations must be a positive integer")
        if not isinstance(self.storage_tighten_feasible_bounds, bool):
            raise ValueError("storage_tighten_feasible_bounds must be a boolean")
        if self.period_indices is not None:
            horizon = half_year_calendar(self.scenario_year, self.scenario_block).horizon
            indices = tuple(self.period_indices)
            if not indices or any(
                isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= horizon
                for index in indices
            ):
                raise ValueError("period_indices must contain valid non-negative hour indices")
            if len(set(indices)) != len(indices):
                raise ValueError("period_indices must not contain duplicates")
            object.__setattr__(self, "period_indices", indices)


def make_production_evaluator(
    config: ProductionEvaluatorConfig,
) -> Callable[[RLPlanState, ReleaseManifest | None], Any]:
    """Build a one-plan callback backed by the production batch evaluator.

    Rule loading occurs once when the callback is created.  Each callback call
    writes one canonical site-level submission and delegates to the existing
    batch evaluator; no dispatch or scoring formula is implemented here.
    """

    if not isinstance(config, ProductionEvaluatorConfig):
        raise TypeError("config must be a ProductionEvaluatorConfig")
    horizon = half_year_calendar(config.scenario_year, config.scenario_block).horizon
    rules = load_scoring_rules(
        config.rules_path,
        artifact_dir=config.artifact_dir,
        horizon=horizon,
    )

    def evaluate(
        state: RLPlanState,
        release_manifest: ReleaseManifest | None,
    ) -> Any:
        if not isinstance(state, RLPlanState):
            raise TypeError("state must be an RLPlanState")
        if (
            state.horizon.year != config.scenario_year
            or state.horizon.round_name != config.scenario_block
        ):
            raise ValueError("plan state scenario does not match evaluator configuration")
        if release_manifest is not None and (
            release_manifest.scenario_year != config.scenario_year
            or release_manifest.scenario_block != config.scenario_block
        ):
            raise ValueError("release manifest scenario does not match evaluator configuration")

        config.submission_dir.mkdir(parents=True, exist_ok=True)
        submission_path = config.submission_dir / (
            f"rl-{state.team_id}-{config.scenario_year}-{config.scenario_block}-"
            f"{uuid.uuid4().hex}.csv"
        )
        evaluation_kwargs: dict[str, Any] = {
            "dispatch_workers": config.dispatch_workers,
            "projection_iterations": config.projection_iterations,
            "storage_solver": config.storage_solver,
            "storage_solver_backend": config.storage_solver_backend,
            "storage_parallel_workers": config.storage_parallel_workers,
            "storage_solver_threads": config.storage_solver_threads,
            "storage_wall_time_limit_seconds": config.storage_wall_time_limit_seconds,
            "storage_max_iterations": config.storage_max_iterations,
            "storage_tighten_feasible_bounds": config.storage_tighten_feasible_bounds,
        }
        if config.period_indices is not None:
            evaluation_kwargs["period_indices"] = config.period_indices
        return evaluate_plan_state(
            state,
            submission_path=submission_path,
            artifact_dir=config.artifact_dir,
            rules=rules,
            **evaluation_kwargs,
        )

    return evaluate


__all__ = ["ProductionEvaluatorConfig", "make_production_evaluator"]
