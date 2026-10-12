"""Phase 1 adapter between RL plan state and the frozen evaluator contract.

This module intentionally stops at plan materialization.  It does not expose a
Gymnasium environment and does not implement ``reset`` or ``step``.  Dispatch
and score calculation remain owned by ``tools.activsg2000.plan_adapter`` and
``ercot_game.batch_evaluation``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
import io
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import tempfile
from typing import Any

from ercot_game import batch_evaluation
from ercot_game.calendars import HalfYearCalendar, half_year_calendar
from ercot_game.csv_submission import (
    CANONICAL_CSV_COLUMNS,
    TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH,
    read_student_csv,
)
from ercot_game.models import SitePlan, TeamPlan
from tools.activsg2000.plan_adapter import normalize_plan_for_adapter


_SCORE_FIELDS = ("score", "served_load_mwh", "it_value")
_COST_FIELDS = (
    "energy_cost",
    "generator_operating_cost",
    "generator_capital_cost",
    "storage_capital_cost",
    "site_cost",
)


@dataclass(frozen=True)
class HalfYearHorizon:
    """Exact released calendar metadata for one half-year episode candidate."""

    year: int
    round_name: str
    horizon_hours: int
    calendar: HalfYearCalendar = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.horizon_hours, int)
            or isinstance(self.horizon_hours, bool)
            or self.horizon_hours <= 0
        ):
            raise ValueError("horizon_hours must be a positive integer")
        calendar = half_year_calendar(self.year, self.round_name)
        if self.horizon_hours != calendar.horizon:
            raise ValueError(
                "horizon_hours must match the released half-year calendar "
                f"({calendar.horizon} for {calendar.label})"
            )
        object.__setattr__(self, "calendar", calendar)

    @classmethod
    def for_round(cls, year: int, round_name: str) -> "HalfYearHorizon":
        calendar = half_year_calendar(year, round_name)
        return cls(calendar.year, calendar.round_name, calendar.horizon)

    @property
    def label(self) -> str:
        return f"{self.year}_{self.round_name}"


@dataclass(frozen=True)
class RLPlanState:
    """Immutable site-level plan state carried by the Phase 1 RL adapter.

    ``sites`` is the frozen seven-column game contract represented by
    :class:`ercot_game.models.SitePlan`.  ``horizon`` is retained here because
    the canonical ``TeamPlan`` does not carry episode metadata.  Calling
    :meth:`to_team_plan` materializes constant hourly connection profiles and
    deterministic private asset IDs for the existing evaluator.
    """

    team_id: str
    sites: tuple[SitePlan, ...]
    horizon: HalfYearHorizon

    def __post_init__(self) -> None:
        if not isinstance(self.team_id, str) or not self.team_id:
            raise ValueError("team_id must be a non-empty string")
        if not isinstance(self.horizon, HalfYearHorizon):
            raise TypeError("horizon must be a HalfYearHorizon")
        sites = tuple(self.sites)
        if any(not isinstance(site, SitePlan) for site in sites):
            raise TypeError("sites must contain only SitePlan entries")
        object.__setattr__(self, "sites", sites)
        _validate_with_public_csv_parser(
            self.team_id,
            sites,
            self.horizon_hours,
        )

    @classmethod
    def for_half_year(
        cls,
        *,
        team_id: str,
        sites: Sequence[SitePlan],
        year: int,
        round_name: str,
    ) -> "RLPlanState":
        return cls(team_id, tuple(sites), HalfYearHorizon.for_round(year, round_name))

    @classmethod
    def from_team_plan(
        cls,
        plan: TeamPlan,
        *,
        year: int,
        round_name: str,
    ) -> "RLPlanState":
        if not isinstance(plan, TeamPlan):
            raise TypeError("plan must be a TeamPlan")
        if not plan.sites:
            raise ValueError("plan must contain canonical SitePlan entries")
        return cls.for_half_year(
            team_id=plan.team_id,
            sites=plan.sites,
            year=year,
            round_name=round_name,
        )

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        year: int,
        round_name: str,
        bus_ids: set[str] | frozenset[str] | None = None,
    ) -> "RLPlanState":
        horizon = HalfYearHorizon.for_round(year, round_name)
        submission = read_student_csv(
            path,
            bus_ids=bus_ids,
            horizon=horizon.horizon_hours,
        )
        return cls(submission.team_id, submission.plan.sites, horizon)

    @property
    def horizon_hours(self) -> int:
        return self.horizon.horizon_hours

    def write_csv(self, path: str | Path) -> Path:
        """Write this state as a canonical CSV and return its persistent path."""
        csv_path = Path(path)
        csv_path.write_text(
            _canonical_csv_text(self.team_id, self.sites),
            encoding="utf-8",
        )
        return csv_path

    def to_team_plan(self) -> TeamPlan:
        """Materialize the evaluator-compatible expanded ``TeamPlan``.

        The existing plan adapter owns connection/generator/storage expansion;
        this method only aligns its compatibility cost scalar with the
        canonical CSV reader. The prepared DCOPF adapter and batch evaluator
        remain responsible for dispatch and all score/cost mathematics.
        """
        site_plan = TeamPlan(team_id=self.team_id, sites=self.sites)
        expanded = normalize_plan_for_adapter(
            site_plan,
            horizon=self.horizon_hours,
        )
        generators = tuple(
            replace(
                generator,
                marginal_cost_per_mwh=TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH,
            )
            for generator in expanded.generators
        )
        return replace(expanded, generators=generators)


def evaluate_plan_state(
    state: RLPlanState,
    *,
    submission_path: str | Path,
    artifact_dir: str | Path,
    rules: Any,
    evaluator: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Evaluate one RL plan through the existing batch evaluator.

    The adapter writes the canonical seven-column site contract to the caller's
    ``submission_path``, then delegates all validation, dispatch, projection,
    and scoring to :func:`ercot_game.batch_evaluation.evaluate_csv_batch`.
    Keeping the path caller-controlled preserves ``SubmissionRecord.path`` in
    the returned evaluation. ``evaluator`` is an explicit seam for lightweight
    tests; production callers should leave it unset. Additional keyword
    arguments are passed through unchanged.
    """
    if not isinstance(state, RLPlanState):
        raise TypeError("state must be an RLPlanState")
    evaluate = batch_evaluation.evaluate_csv_batch if evaluator is None else evaluator
    csv_path = state.write_csv(submission_path)
    return evaluate(
        (csv_path,),
        artifact_dir=artifact_dir,
        horizon=state.horizon_hours,
        rules=rules,
        **kwargs,
    )


@dataclass(frozen=True)
class ParityReport:
    """Structural and optional evaluator-result comparison for one plan."""

    canonical_structure_equal: bool
    score_fields_equal: bool | None
    cost_fields_equal: bool | None
    differences: tuple[str, ...]
    adapter_plan: TeamPlan
    reference_plan: TeamPlan


def compare_plan_parity(
    state: RLPlanState,
    reference_plan: TeamPlan,
    *,
    adapter_score: Any | None = None,
    reference_score: Any | None = None,
    tolerance: float = 1e-9,
) -> ParityReport:
    """Compare adapter output with an evaluator-facing reference plan.

    Scores are optional so this helper can be used without solver fixtures.
    When both scores are present, only fields emitted by the existing
    evaluator are compared; no scoring formula is reproduced here.
    """
    if not isinstance(reference_plan, TeamPlan):
        raise TypeError("reference_plan must be a TeamPlan")
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and non-negative")
    adapter_plan = state.to_team_plan()
    structure_equal = _canonical_structure(adapter_plan) == _canonical_structure(reference_plan)
    differences: list[str] = []
    if not structure_equal:
        differences.append("canonical_structure")

    score_equal: bool | None = None
    cost_equal: bool | None = None
    adapter_score_record = _score_record(adapter_score, state.team_id)
    reference_score_record = _score_record(reference_score, state.team_id)
    if adapter_score_record is not None and reference_score_record is not None:
        score_equal = _fields_equal(
            adapter_score_record,
            reference_score_record,
            _SCORE_FIELDS,
            tolerance,
        )
        cost_equal = _fields_equal(
            adapter_score_record,
            reference_score_record,
            _COST_FIELDS,
            tolerance,
        )
        differences.extend(
            field
            for field in (*_SCORE_FIELDS, *_COST_FIELDS)
            if not _field_equal(
                adapter_score_record,
                reference_score_record,
                field,
                tolerance,
            )
        )

    return ParityReport(
        canonical_structure_equal=structure_equal,
        score_fields_equal=score_equal,
        cost_fields_equal=cost_equal,
        differences=tuple(dict.fromkeys(differences)),
        adapter_plan=adapter_plan,
        reference_plan=reference_plan,
    )


def compare_csv_parity(
    state: RLPlanState,
    csv_path: str | Path,
    *,
    bus_ids: set[str] | frozenset[str] | None = None,
    adapter_score: Any | None = None,
    csv_score: Any | None = None,
    tolerance: float = 1e-9,
) -> ParityReport:
    """Compare a plan state with a canonical CSV-derived ``TeamPlan``.

    A caller may pass scores returned by ``evaluate_csv_batch`` (or the
    corresponding ``TeamScore`` records) to extend the comparison to score
    and cost fields when a real evaluator fixture is available.
    """
    submission = read_student_csv(
        csv_path,
        bus_ids=bus_ids,
        horizon=state.horizon_hours,
    )
    return compare_plan_parity(
        state,
        submission.plan,
        adapter_score=adapter_score,
        reference_score=csv_score,
        tolerance=tolerance,
    )


def _canonical_csv_text(team_id: str, sites: Sequence[SitePlan]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CANONICAL_CSV_COLUMNS)
    for site in sites:
        writer.writerow(
            (
                team_id,
                site.site_id,
                site.bus_id,
                site.connection_capacity_mw,
                site.generator_capacity_mw,
                site.storage_power_mw,
                site.storage_energy_mwh,
            )
        )
    return output.getvalue()


def _validate_with_public_csv_parser(
    team_id: str,
    sites: Sequence[SitePlan],
    horizon_hours: int,
) -> None:
    """Apply the public canonical CSV constraints without private imports."""
    with tempfile.TemporaryDirectory(prefix="rl-gym-validate-") as directory:
        path = Path(directory) / "plan.csv"
        path.write_text(_canonical_csv_text(team_id, sites), encoding="utf-8")
        parsed = read_student_csv(path, horizon=horizon_hours)
    if parsed.team_id != team_id or parsed.plan.sites != tuple(sites):
        raise ValueError("RL plan does not round-trip through the canonical CSV contract")


def _canonical_structure(plan: TeamPlan) -> tuple[object, ...]:
    sites = tuple(
        (
            site.site_id,
            site.bus_id,
            site.connection_capacity_mw,
            site.generator_capacity_mw,
            site.storage_power_mw,
            site.storage_energy_mwh,
        )
        for site in sorted(plan.sites, key=lambda item: item.site_id)
    )
    connections = tuple(
        (
            item.connection_id,
            item.bus_id,
            item.capacity_mw,
            tuple(item.profile),
        )
        for item in sorted(plan.connections, key=lambda item: item.connection_id)
    )
    generators = tuple(
        (
            item.generator_id,
            item.connection_id,
            item.capacity_mw,
            item.marginal_cost_per_mwh,
        )
        for item in sorted(plan.generators, key=lambda item: item.generator_id)
    )
    storages = tuple(
        (
            item.storage_id,
            item.connection_id,
            item.power_mw,
            item.energy_mwh,
            item.initial_state_of_charge_mwh,
        )
        for item in sorted(plan.storages, key=lambda item: item.storage_id)
    )
    return plan.team_id, sites, connections, generators, storages


def _value(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record[field]
    return getattr(record, field)


def _score_record(evaluation_or_score: Any | None, team_id: str) -> Any | None:
    if evaluation_or_score is None:
        return None
    if isinstance(evaluation_or_score, Mapping):
        team_scores = evaluation_or_score.get("team_scores")
    else:
        team_scores = getattr(evaluation_or_score, "team_scores", None)
    if team_scores is None:
        return evaluation_or_score
    for score in team_scores:
        if _value(score, "team_id") == team_id:
            return score
    raise ValueError(f"evaluation does not contain a score for team {team_id!r}")


def _field_equal(left: Any, right: Any, field: str, tolerance: float) -> bool:
    left_value = _value(left, field)
    right_value = _value(right, field)
    if isinstance(left_value, bool) or isinstance(right_value, bool):
        return left_value == right_value
    if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
        return math.isclose(left_value, right_value, rel_tol=tolerance, abs_tol=tolerance)
    return left_value == right_value


def _fields_equal(left: Any, right: Any, fields: Sequence[str], tolerance: float) -> bool:
    return all(_field_equal(left, right, field, tolerance) for field in fields)


__all__ = [
    "HalfYearHorizon",
    "ParityReport",
    "RLPlanState",
    "compare_csv_parity",
    "compare_plan_parity",
    "evaluate_plan_state",
]
