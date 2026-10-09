"""Deterministic instructor-side CSV collection and batch evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import csv
import heapq
import hashlib
import json
from copy import copy
from pathlib import Path
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import math
import shutil
from typing import Any, TYPE_CHECKING, NoReturn

from .csv_submission import (
    CSVSubmission,
    TEAM_GENERATOR_COST_CURVE,
    load_bus_ids,
    read_student_csv,
    read_submission_identity,
)
from .asset_state import AssetLedger, AssetStateError
from .calendars import SUPPORTED_YEARS, half_year_calendar
from .models import TeamPlan
from .models import ConnectionPlan
from .economic_profiles import (
    HourlyEconomicProfiles,
    load_economic_profiles,
    load_economic_profiles_artifact,
)


_RELEASED_HALF_YEAR_HORIZONS = frozenset(
    half_year_calendar(year, round_name).horizon
    for year in SUPPORTED_YEARS
    for round_name in ("jan_jun", "jul_dec")
)

if TYPE_CHECKING:
    from tools.activsg2000.dcopf import DCOPFInfeasible, DCOPFUnresolved
    from tools.activsg2000.plan_adapter import (
        BatchDispatchResult,
        evaluate_plans_with_prepared_dcopf,
    )
else:  # pragma: no cover - runtime branch is exercised by import behavior
    try:
        from tools.activsg2000.dcopf import (
            DCOPFInfeasible as _runtime_dcopf_infeasible,
            DCOPFUnresolved as _runtime_dcopf_unresolved,
        )
        from tools.activsg2000.plan_adapter import (
            BatchDispatchResult as _runtime_batch_dispatch_result,
            evaluate_plans_with_prepared_dcopf as _runtime_evaluate_plans,
        )
    except ModuleNotFoundError:
        class _FallbackDCOPFInfeasible(ValueError):
            """Fallback exception when the optional tools package is not importable."""

        _runtime_dcopf_infeasible: Any = _FallbackDCOPFInfeasible
        _runtime_dcopf_unresolved: Any = _FallbackDCOPFInfeasible
        _runtime_batch_dispatch_result: Any = object

        def _runtime_evaluate_plans(
            artifact_dir: str | Path,
            plans: Sequence[TeamPlan],
            *,
            period_indices: tuple[int, ...],
        ) -> NoReturn:
            raise RuntimeError("the ACTIVSg2000 adapter is not importable")

    globals().update(
        {
            "DCOPFInfeasible": _runtime_dcopf_infeasible,
            "DCOPFUnresolved": _runtime_dcopf_unresolved,
            "BatchDispatchResult": _runtime_batch_dispatch_result,
            "evaluate_plans_with_prepared_dcopf": _runtime_evaluate_plans,
        }
    )


@dataclass(frozen=True)
class SubmissionRecord:
    path: Path
    team_id: str
    submission_id: str | None
    submission: CSVSubmission | None
    valid: bool
    failure_reason: str | None
    identity_is_fallback: bool = False
    input_sha256: str | None = None

    @property
    def submission_key(self) -> str:
        """Return a deterministic identity that remains unique across team IDs."""
        return str(self.path.resolve())


@dataclass(frozen=True)
class BatchCollection:
    accepted: tuple[SubmissionRecord, ...]
    rejected: tuple[SubmissionRecord, ...]


def _validate_rule_number(name: str, value: float, *, nonnegative: bool = False, positive: bool = False) -> None:
    try:
        finite = math.isfinite(value)
    except TypeError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class BatchScoringRules:
    rules_version: str
    it_value_per_mwh: tuple[float, ...] = ()
    energy_price_per_mwh: tuple[float, ...] = ()
    time_step_hours: float = 1.0
    generator_capital_cost_per_mw: float = 0.0
    site_cost_per_mw: float = 0.0
    # Retained for callers that load pre-Task-5 rules; production rules use
    # the capacity-dependent site coefficient above.
    site_cost_per_site: float = 0.0
    infeasible_score: float = 0.0
    default_generator_cost_per_mwh: float = 100.0
    storage_capital_cost_per_mw: float = 0.0
    storage_capital_cost_per_mwh: float = 0.0
    economic_profiles: HourlyEconomicProfiles | None = None

    def __post_init__(self) -> None:
        for field_name in ("it_value_per_mwh", "energy_price_per_mwh"):
            try:
                values = tuple(getattr(self, field_name))
            except TypeError:
                raise ValueError(f"{field_name} must contain finite values") from None
            object.__setattr__(self, field_name, values)
            for index, value in enumerate(values):
                _validate_rule_number(f"{field_name}[{index}]", value, nonnegative=True)
        _validate_rule_number("time_step_hours", self.time_step_hours, positive=True)
        _validate_rule_number(
            "generator_capital_cost_per_mw",
            self.generator_capital_cost_per_mw,
            nonnegative=True,
        )
        _validate_rule_number("site_cost_per_site", self.site_cost_per_site, nonnegative=True)
        _validate_rule_number("site_cost_per_mw", self.site_cost_per_mw, nonnegative=True)
        _validate_rule_number("infeasible_score", self.infeasible_score)
        _validate_rule_number(
            "default_generator_cost_per_mwh",
            self.default_generator_cost_per_mwh,
            nonnegative=True,
        )
        for name in ("storage_capital_cost_per_mw", "storage_capital_cost_per_mwh"):
            _validate_rule_number(name, getattr(self, name), nonnegative=True)
        if self.economic_profiles is not None and not isinstance(self.economic_profiles, HourlyEconomicProfiles):
            raise ValueError("economic_profiles must be an HourlyEconomicProfiles instance")


@dataclass(frozen=True)
class CapitalCostScope:
    """One-time construction-cost scope for a multi-round evaluation.

    When omitted from :func:`evaluate_csv_batch`, every asset in the
    evaluator-facing plan is charged.  When supplied, only the listed asset
    IDs are charged capital cost; operational costs still use the complete
    materialized plan.
    """

    generator_ids_by_team: Mapping[str, Iterable[str]] = field(default_factory=dict)
    storage_ids_by_team: Mapping[str, Iterable[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "generator_ids_by_team",
            _normalize_capital_ids(self.generator_ids_by_team, "generator"),
        )
        object.__setattr__(
            self,
            "storage_ids_by_team",
            _normalize_capital_ids(self.storage_ids_by_team, "storage"),
        )

    def generator_ids_for(self, team_id: str) -> frozenset[str]:
        return self.generator_ids_by_team.get(team_id, frozenset())

    def storage_ids_for(self, team_id: str) -> frozenset[str]:
        return self.storage_ids_by_team.get(team_id, frozenset())


def _normalize_capital_ids(
    values: Mapping[str, Iterable[str]],
    kind: str,
) -> dict[str, frozenset[str]]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{kind}_ids_by_team must be a mapping")
    normalized: dict[str, frozenset[str]] = {}
    for team_id, asset_ids in values.items():
        if not isinstance(team_id, str) or not team_id:
            raise ValueError(f"{kind}_ids_by_team keys must be non-empty strings")
        if isinstance(asset_ids, (str, bytes)):
            raise ValueError(f"{kind}_ids_by_team values must be collections of IDs")
        try:
            normalized[team_id] = frozenset(asset_ids)
        except TypeError:
            raise ValueError(f"{kind}_ids_by_team values must be collections of IDs") from None
        if any(not isinstance(asset_id, str) or not asset_id for asset_id in normalized[team_id]):
            raise ValueError(f"{kind}_ids_by_team values must contain non-empty string IDs")
    return normalized


@dataclass(frozen=True)
class TeamScore:
    team_id: str
    rank: int
    valid: bool
    feasible: bool
    score: float
    served_load_mwh: float
    it_value: float
    energy_cost: float
    generator_operating_cost: float
    generator_capital_cost: float
    site_cost: float
    competition_scope: str
    failure_reason: str | None = None
    submission_key: str | None = None
    storage_capital_cost: float = 0.0


@dataclass(frozen=True)
class FailureIsolationEvent:
    attempted_team_ids: tuple[str, ...]
    removed_team_id: str | None
    outcome: str
    reason: str


@dataclass(frozen=True)
class DispatchRecord:
    """Audit record for one joint, subset, or individual dispatch attempt."""

    scope: str
    attempted_team_ids: tuple[str, ...]
    attempted_submission_keys: tuple[str, ...]
    status: str
    dispatch: BatchDispatchResult | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class ProjectionDiagnostics:
    """Audit data for the continuous joint-load projection."""

    service_fraction_by_team: dict[str, float]
    projected_capacity_mw_by_team: dict[str, float]
    capped_request_capacity_mw_by_team: dict[str, float]
    requested_load_mwh_by_team: dict[str, float]
    projected_load_mwh_by_team: dict[str, float]
    total_requested_load_mwh: float
    total_projected_load_mwh: float
    proportional_fair_utility: float
    total_load_tolerance_mwh: float
    objective: str = "total_served_load_then_proportional_fair"
    cap_policy: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchEvaluation:
    rules_version: str
    competition_scope: str
    input_sha256_by_team: dict[str, str]
    team_scores: tuple[TeamScore, ...]
    rejected_submissions: tuple[SubmissionRecord, ...]
    isolation_events: tuple[FailureIsolationEvent, ...]
    dispatch: BatchDispatchResult | None
    failure_reason: str | None = None
    rules_payload: dict[str, Any] | None = None
    accepted_submissions: tuple[SubmissionRecord, ...] = ()
    input_sha256_by_submission: dict[str, str | None] = field(default_factory=dict)
    dispatch_records: tuple[DispatchRecord, ...] = ()
    source_period_indices: tuple[int, ...] = ()
    projection_diagnostics: ProjectionDiagnostics | None = None


def load_scoring_rules(
    path: str | Path,
    *,
    artifact_dir: str | Path | None = None,
    horizon: int | None = None,
) -> BatchScoringRules:
    """Load scoring rules, optionally resolving the production profile artifact."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("scoring rules must be a JSON object")
    economic_profiles = None
    economic_manifest = (
        Path(artifact_dir) / "economic_profile_manifest.json"
        if artifact_dir is not None
        else None
    )
    if economic_manifest is not None and (
        economic_manifest.is_file()
        or horizon in _RELEASED_HALF_YEAR_HORIZONS
    ):
        economic_profiles = load_economic_profiles_artifact(
            artifact_dir,
            expected_horizon=horizon,
        )
    if isinstance(payload.get("economic_profiles"), Mapping):
        inline_profiles = load_economic_profiles(payload["economic_profiles"])
        if economic_profiles is not None and inline_profiles != economic_profiles:
            raise ValueError("inline economic profiles conflict with the production artifact")
        economic_profiles = inline_profiles
    if economic_profiles is not None:
        it_values: tuple[float, ...] = ()
        energy_prices: tuple[float, ...] = ()
    else:
        it_values = tuple(float(value) for value in payload.get("it_value_per_mwh", ()))
        energy_prices = tuple(float(value) for value in payload.get("energy_price_per_mwh", ()))
    return BatchScoringRules(
        rules_version=str(payload["rules_version"]),
        it_value_per_mwh=it_values,
        energy_price_per_mwh=energy_prices,
        time_step_hours=float(payload.get("time_step_hours", 1.0)),
        generator_capital_cost_per_mw=float(payload.get("generator_capital_cost_per_mw", 0.0)),
        site_cost_per_mw=float(payload.get("site_cost_per_mw", 0.0)),
        site_cost_per_site=float(payload.get("site_cost_per_site", 0.0)),
        infeasible_score=float(payload.get("infeasible_score", 0.0)),
        default_generator_cost_per_mwh=float(payload.get("default_generator_cost_per_mwh", 100.0)),
        storage_capital_cost_per_mw=float(payload.get("storage_capital_cost_per_mw", 0.0)),
        storage_capital_cost_per_mwh=float(payload.get("storage_capital_cost_per_mwh", 0.0)),
        economic_profiles=economic_profiles,
    )


def _unparsed_team_id(path: Path) -> str:
    return f"UNPARSED::{path.name}"


def _full_path_sort_key(path: Path) -> str:
    return str(path.resolve())


def _collect_paths(
    paths: Sequence[str | Path],
    *,
    region_to_source_bus: Mapping[str, str] | None,
    bus_ids: set[str] | frozenset[str] | None = None,
    generator_cost_by_id: Mapping[str, float] | None = None,
    horizon: int,
    default_generator_cost_per_mwh: float,
    require_complete_horizon: bool = False,
) -> BatchCollection:
    records: list[SubmissionRecord] = []
    for raw_path in sorted((Path(path) for path in paths), key=_full_path_sort_key):
        declared_team_id: str | None = None
        declared_submission_id: str | None = None
        input_sha256: str | None = None
        try:
            input_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            declared_team_id, declared_submission_id = read_submission_identity(raw_path)
            submission = read_student_csv(
                raw_path,
                region_to_source_bus=region_to_source_bus,
                bus_ids=bus_ids,
                generator_cost_by_id=generator_cost_by_id,
                horizon=horizon,
                default_generator_cost_per_mwh=default_generator_cost_per_mwh,
                require_complete_horizon=require_complete_horizon,
                allow_legacy=True,
            )
        except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
            records.append(
                SubmissionRecord(
                    path=raw_path,
                    team_id=declared_team_id or _unparsed_team_id(raw_path),
                    submission_id=declared_submission_id,
                    submission=None,
                    valid=False,
                    failure_reason=str(exc),
                    identity_is_fallback=declared_team_id is None,
                    input_sha256=input_sha256,
                )
            )
        else:
            records.append(
                SubmissionRecord(
                    path=raw_path,
                    team_id=submission.team_id,
                    submission_id=submission.submission_id,
                    submission=submission,
                    valid=True,
                    failure_reason=None,
                    input_sha256=submission.sha256,
                )
            )

    team_counts = Counter(record.team_id for record in records)
    accepted: list[SubmissionRecord] = []
    rejected: list[SubmissionRecord] = []
    for record in records:
        if team_counts[record.team_id] > 1:
            duplicate_reason = f"duplicate team_id: {record.team_id}"
            failure_reason = (
                f"{duplicate_reason}; {record.failure_reason}"
                if record.failure_reason
                else duplicate_reason
            )
            rejected.append(
                replace(
                    record,
                    submission=None,
                    valid=False,
                    failure_reason=failure_reason,
                )
            )
        elif record.valid:
            accepted.append(record)
        else:
            rejected.append(record)

    accepted.sort(key=lambda record: record.team_id)
    rejected.sort(key=lambda record: _full_path_sort_key(record.path))
    return BatchCollection(tuple(accepted), tuple(rejected))


def collect_csv_submissions(
    directory: str | Path,
    *,
    region_to_source_bus: Mapping[str, str] | None = None,
    bus_ids: set[str] | frozenset[str] | None = None,
    generator_cost_by_id: Mapping[str, float] | None = None,
    horizon: int,
    default_generator_cost_per_mwh: float,
    require_complete_horizon: bool = False,
) -> BatchCollection:
    """Discover, validate, and deterministically partition CSV submissions."""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"submission directory does not exist or is not a directory: {root}")
    paths = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() == ".csv"
    ]
    return _collect_paths(
        paths,
        region_to_source_bus=region_to_source_bus,
        bus_ids=bus_ids,
        generator_cost_by_id=generator_cost_by_id,
        horizon=horizon,
        default_generator_cost_per_mwh=default_generator_cost_per_mwh,
        require_complete_horizon=require_complete_horizon,
    )


def _collect_evaluation_inputs(
    csv_paths: Sequence[str | Path] | str | Path,
    *,
    region_to_source_bus: Mapping[str, str] | None = None,
    bus_ids: set[str] | frozenset[str] | None = None,
    generator_cost_by_id: Mapping[str, float] | None = None,
    horizon: int,
    default_generator_cost_per_mwh: float,
    require_complete_horizon: bool = False,
) -> BatchCollection:
    if isinstance(csv_paths, (str, Path)):
        root = Path(csv_paths)
        if root.is_dir():
            return collect_csv_submissions(
                root,
                region_to_source_bus=region_to_source_bus,
                bus_ids=bus_ids,
                generator_cost_by_id=generator_cost_by_id,
                horizon=horizon,
                default_generator_cost_per_mwh=default_generator_cost_per_mwh,
                require_complete_horizon=require_complete_horizon,
            )
        paths: Sequence[str | Path] = (root,)
    else:
        paths = csv_paths
    return _collect_paths(
        paths,
        region_to_source_bus=region_to_source_bus,
        bus_ids=bus_ids,
        generator_cost_by_id=generator_cost_by_id,
        horizon=horizon,
        default_generator_cost_per_mwh=default_generator_cost_per_mwh,
        require_complete_horizon=require_complete_horizon,
    )


def _score_team(
    plan: TeamPlan,
    dispatch: BatchDispatchResult,
    rules: BatchScoringRules,
    *,
    submission_key: str | None = None,
    capital_cost_scope: CapitalCostScope | None = None,
    site_cost_connection_count_by_team: Mapping[str, int] | None = None,
    site_cost_connection_capacity_mw_by_team: Mapping[str, float] | None = None,
    submission_number: int | None = None,
) -> TeamScore:
    if submission_number not in (None, 1, 2):
        raise ValueError("submission_number must be 1, 2, or None")
    period_indices = tuple(period.period_index for period in dispatch.periods)
    if len(set(period_indices)) != len(period_indices):
        raise ValueError("dispatch contains duplicate period index entries")
    periods_by_index = {period.period_index: period for period in dispatch.periods}
    team_id = plan.team_id
    generator_by_id = {generator.generator_id: generator for generator in plan.generators}
    served = it_value = energy_cost = generator_cost = 0.0
    for period_index, period in periods_by_index.items():
        load = period.submitted_load_mw_by_team[team_id]
        generation = sum(period.generator_dispatch_mw_by_team[team_id].values())
        profiles = rules.economic_profiles
        value = profiles.it_value_for(period_index) if profiles is not None else rules.it_value_per_mwh[period_index]
        served += load * rules.time_step_hours
        it_value += load * value * rules.time_step_hours
        if submission_number != 1:
            grid_purchase_by_team = getattr(period, "grid_purchase_mw_by_team", None)
            purchases_by_bus = getattr(period, "grid_purchase_mw_by_team_by_bus", None)
            lmp_by_bus = getattr(getattr(period, "dcopf", None), "balance_dual_by_bus", {}) or {}

            def settlement_price(bus_id: str) -> float:
                if bus_id in lmp_by_bus:
                    return max(0.0, float(lmp_by_bus[bus_id]))
                if profiles is not None:
                    return profiles.price_for_bus(bus_id, period_index)
                return rules.energy_price_per_mwh[period_index]

            if purchases_by_bus is not None and team_id in purchases_by_bus:
                energy_cost += sum(
                    amount * settlement_price(bus_id)
                    for bus_id, amount in purchases_by_bus[team_id].items()
                ) * rules.time_step_hours
            else:
                if grid_purchase_by_team is not None:
                    grid_purchase = grid_purchase_by_team.get(team_id, max(0.0, load - generation))
                else:
                    grid_purchase = max(0.0, load - generation)
                if plan.connections and lmp_by_bus:
                    fallback_bus = plan.connections[0].bus_id
                    price = settlement_price(fallback_bus)
                elif profiles is not None:
                    price = profiles.price_for_bus(sorted(profiles.bus_ids)[0], period_index)
                else:
                    price = rules.energy_price_per_mwh[period_index]
                energy_cost += grid_purchase * price * rules.time_step_hours
        period_generator_cost = 0.0
        for generator_id, amount in period.generator_dispatch_mw_by_team[team_id].items():
            generator = generator_by_id.get(generator_id)
            if generator is None:
                if amount != 0:
                    raise ValueError(
                        f"team {team_id}: unknown generator dispatch {generator_id!r} "
                        f"has nonzero amount {amount}"
                    )
                continue
            remaining = max(0.0, min(float(amount), generator.capacity_mw))
            for fraction, marginal_cost in TEAM_GENERATOR_COST_CURVE:
                segment_amount = min(remaining, generator.capacity_mw * fraction)
                period_generator_cost += segment_amount * marginal_cost
                remaining -= segment_amount
                if remaining <= 0:
                    break
        generator_cost += period_generator_cost * rules.time_step_hours
    generator_ids_for_capital = (
        None
        if capital_cost_scope is None
        else capital_cost_scope.generator_ids_for(team_id)
    )
    storage_ids_for_capital = (
        None
        if capital_cost_scope is None
        else capital_cost_scope.storage_ids_for(team_id)
    )
    generator_capital = sum(
        generator.capacity_mw
        for generator in plan.generators
        if generator_ids_for_capital is None or generator.generator_id in generator_ids_for_capital
    ) * rules.generator_capital_cost_per_mw
    storage_capital = (
        sum(
            storage.power_mw
            for storage in plan.storages
            if storage_ids_for_capital is None or storage.storage_id in storage_ids_for_capital
        ) * rules.storage_capital_cost_per_mw
        + sum(
            storage.energy_mwh
            for storage in plan.storages
            if storage_ids_for_capital is None or storage.storage_id in storage_ids_for_capital
        ) * rules.storage_capital_cost_per_mwh
    )
    site_capacity = (
        sum(connection.capacity_mw for connection in plan.connections)
        if site_cost_connection_capacity_mw_by_team is None
        else site_cost_connection_capacity_mw_by_team.get(
            team_id, sum(connection.capacity_mw for connection in plan.connections)
        )
    )
    if rules.site_cost_per_mw:
        site_cost = site_capacity * rules.site_cost_per_mw
    else:
        site_count = (
            len(plan.connections)
            if site_cost_connection_count_by_team is None
            else site_cost_connection_count_by_team.get(team_id, len(plan.connections))
        )
        site_cost = site_count * rules.site_cost_per_site
    score = it_value - energy_cost - generator_cost - generator_capital - storage_capital - site_cost
    return TeamScore(
        team_id=team_id,
        rank=0,
        valid=True,
        feasible=True,
        score=score,
        served_load_mwh=served,
        it_value=it_value,
        energy_cost=energy_cost,
        generator_operating_cost=generator_cost,
        generator_capital_cost=generator_capital,
        site_cost=site_cost,
        storage_capital_cost=storage_capital,
        competition_scope="joint",
        submission_key=submission_key,
    )


def _infeasible_score(
    team_id: str,
    rules: BatchScoringRules,
    *,
    valid: bool,
    reason: str,
    competition_scope: str = "not_evaluated",
    submission_key: str | None = None,
) -> TeamScore:
    return TeamScore(
        team_id=team_id,
        rank=0,
        valid=valid,
        feasible=False,
        score=rules.infeasible_score,
        served_load_mwh=0.0,
        it_value=0.0,
        energy_cost=0.0,
        generator_operating_cost=0.0,
        generator_capital_cost=0.0,
        site_cost=0.0,
        storage_capital_cost=0.0,
        competition_scope=competition_scope,
        failure_reason=reason,
        submission_key=submission_key,
    )


def _validate_rules(rules: BatchScoringRules, horizon: int, period_indices: tuple[int, ...]) -> None:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if not period_indices:
        raise ValueError("period_indices must be non-empty")
    if any(not isinstance(index, int) or isinstance(index, bool) for index in period_indices):
        raise ValueError("period_indices must contain integers")
    if any(index < 0 for index in period_indices):
        raise ValueError("period_indices must be non-empty and non-negative")
    if len(set(period_indices)) != len(period_indices):
        raise ValueError("period_indices must not contain duplicates")
    if any(index >= horizon for index in period_indices):
        raise ValueError("period_indices must be less than horizon")
    if rules.economic_profiles is not None:
        if max(period_indices) >= rules.economic_profiles.horizon_hours:
            raise ValueError("economic profile must cover the selected period indices")
    elif max(period_indices) >= len(rules.it_value_per_mwh) or max(period_indices) >= len(rules.energy_price_per_mwh):
        raise ValueError("scoring price/value vectors must cover the selected period indices")
    if not math.isfinite(rules.time_step_hours) or rules.time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")


def _default_source_period_offset(horizon: int) -> int:
    """Return the source-row offset for the two supported half-year horizons."""
    return 4368 if horizon == 4416 else 0


def _validate_period_mapping(
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...],
) -> None:
    if len(period_indices) != len(source_period_indices):
        raise ValueError(
            "source_period_indices must have the same length as period_indices"
        )
    if any(
        not isinstance(index, int) or isinstance(index, bool)
        for index in source_period_indices
    ):
        raise ValueError("source_period_indices must contain integers")
    if any(index < 0 for index in source_period_indices):
        raise ValueError("source_period_indices must be non-negative")
    if len(set(source_period_indices)) != len(source_period_indices):
        raise ValueError("source_period_indices must not contain duplicates")


def _source_indexed_plans(
    plans: Sequence[TeamPlan],
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...],
) -> tuple[TeamPlan, ...]:
    """Remap local profile positions to the source artifact row positions.

    The adapter intentionally continues to receive its historical
    ``period_indices`` argument.  This boundary adapter expands each profile
    only when source rows differ from local scoring rows, allowing the
    unmodified adapter to solve the correct source rows while downstream
    scoring still sees local period numbers.
    """
    if period_indices == source_period_indices:
        return tuple(plans)
    if not source_period_indices:
        raise ValueError("source_period_indices must be non-empty")
    profile_length = max(source_period_indices) + 1
    remapped: list[TeamPlan] = []
    for plan in plans:
        connections = []
        for connection in plan.connections:
            profile = tuple(connection.profile)
            source_profile = [0.0] * profile_length
            for local_index, source_index in zip(period_indices, source_period_indices):
                if local_index >= len(profile):
                    raise ValueError(
                        f"connection {connection.connection_id} profile is too short "
                        f"for local period {local_index}"
                    )
                source_profile[source_index] = profile[local_index]
            connections.append(
                ConnectionPlan(
                    connection.connection_id,
                    connection.bus_id,
                    connection.capacity_mw,
                    tuple(source_profile),
                )
            )
        remapped.append(
            TeamPlan(
                plan.team_id,
                connections=tuple(connections),
                generators=plan.generators,
                storages=plan.storages,
                sites=plan.sites,
            )
        )
    return tuple(remapped)


def _expand_plans_for_horizon(
    plans: Sequence[TeamPlan],
    horizon: int,
) -> tuple[TeamPlan, ...]:
    """Materialize constant-capacity site plans for the selected horizon."""
    try:
        return tuple(
            AssetLedger.empty().materialize_plan(plan, horizon=horizon)
            for plan in plans
        )
    except AssetStateError as exc:
        raise ValueError(f"could not expand site plans for evaluation: {exc}") from exc


def _relabel_dispatch_periods(
    dispatch: BatchDispatchResult,
    period_indices: tuple[int, ...],
) -> BatchDispatchResult:
    """Expose local scoring indices after solving source-artifact rows."""
    periods = tuple(dispatch.periods)
    if len(periods) != len(period_indices):
        raise ValueError(
            "dispatch returned a different number of periods than requested"
        )
    relabeled_periods = []
    for period, local_index in zip(periods, period_indices):
        try:
            relabeled_periods.append(replace(period, period_index=local_index))
        except TypeError:
            # Lightweight test doubles and legacy adapters may not use
            # dataclasses; preserve their shape while changing only the index.
            relabeled = copy(period)
            relabeled.period_index = local_index
            relabeled_periods.append(relabeled)
    try:
        return replace(dispatch, periods=tuple(relabeled_periods))
    except TypeError:
        relabeled_dispatch = copy(dispatch)
        relabeled_dispatch.periods = tuple(relabeled_periods)
        return relabeled_dispatch


def _scaled_plan(plan: TeamPlan, alpha: float) -> TeamPlan:
    """Scale a team's load while keeping canonical and legacy site views equal."""
    connections = tuple(
        ConnectionPlan(
            connection.connection_id,
            connection.bus_id,
            connection.capacity_mw * alpha,
            connection.profile,
        )
        for connection in plan.connections
    )
    connection_capacity_by_id = {
        connection.connection_id: connection.capacity_mw
        for connection in plan.connections
    }
    sites = tuple(
        replace(
            site,
            connection_capacity_mw=(
                connection_capacity_by_id.get(
                    site.site_id,
                    site.connection_capacity_mw,
                )
                * alpha
            ),
        )
        for site in plan.sites
    )
    return TeamPlan(
        plan.team_id,
        connections=connections,
        generators=plan.generators,
        storages=plan.storages,
        sites=sites,
    )


def _scaled_plan_to_cap(capped_plan: TeamPlan, alpha: float) -> TeamPlan:
    """Scale a pre-capped plan without changing its assets or profiles."""
    return _scaled_plan(capped_plan, alpha)


@dataclass(frozen=True)
class FairProjectionResult:
    """Continuous projection result with a compatibility tuple interface."""

    dispatch: BatchDispatchResult
    service_fraction_by_team: dict[str, float]
    projected_capacity_mw_by_team: dict[str, float]
    projected_plans: tuple[TeamPlan, ...]
    diagnostics: ProjectionDiagnostics

    @property
    def capped_request_capacity_mw_by_team(self) -> dict[str, float]:
        return self.diagnostics.capped_request_capacity_mw_by_team

    @property
    def total_projected_load_mwh(self) -> float:
        return self.diagnostics.total_projected_load_mwh

    @property
    def proportional_fair_utility(self) -> float:
        return self.diagnostics.proportional_fair_utility

    def __iter__(self):
        """Preserve the old three-value unpacking for existing callers/tests."""
        yield self.dispatch
        if len(self.service_fraction_by_team) == 1:
            yield next(iter(self.service_fraction_by_team.values()))
        else:
            yield self.service_fraction_by_team
        yield self.projected_plans


_FAIR_PROJECTION_MIN_SERVICE_FRACTION = 1e-6
_FAIR_PROJECTION_TOLERANCE = 1e-6
_FAIR_PROJECTION_TOTAL_LOAD_TOLERANCE = 1e-6
_FAIR_PROJECTION_SITE_CAPACITY_MW = 100.0
_FAIR_PROJECTION_MAX_TEAM_CAPACITY_MW = 500.0
_FAIR_PROJECTION_MAX_TEAM_TO_MIN_REQUEST_RATIO = 1.0


def _projection_capped_plans(
    plans: Sequence[TeamPlan],
) -> tuple[tuple[TeamPlan, ...], dict[str, float]]:
    """Apply fixed site, team, and multi-team share caps before projection."""
    prelim: dict[str, TeamPlan] = {}
    prelim_capacity: dict[str, float] = {}
    for plan in plans:
        connections = tuple(
            ConnectionPlan(
                connection.connection_id,
                connection.bus_id,
                min(connection.capacity_mw, _FAIR_PROJECTION_SITE_CAPACITY_MW),
                connection.profile,
            )
            for connection in plan.connections
        )
        capped_plan = TeamPlan(
            plan.team_id,
            connections=connections,
            generators=plan.generators,
            storages=plan.storages,
            sites=plan.sites,
        )
        prelim[plan.team_id] = capped_plan
        prelim_capacity[plan.team_id] = sum(connection.capacity_mw for connection in connections)

    positive_capacities = tuple(
        capacity for capacity in prelim_capacity.values() if capacity > 0.0
    )
    minimum_positive_capacity = min(positive_capacities, default=0.0)
    equalized_request_cap = (
        _FAIR_PROJECTION_MAX_TEAM_TO_MIN_REQUEST_RATIO * minimum_positive_capacity
        if len(positive_capacities) > 1
        else float("inf")
    )
    capped_capacity: dict[str, float] = {}
    for team_id, capacity in prelim_capacity.items():
        team_cap = min(capacity, _FAIR_PROJECTION_MAX_TEAM_CAPACITY_MW)
        if len(positive_capacities) > 1:
            team_cap = min(team_cap, equalized_request_cap)
        capped_capacity[team_id] = team_cap

    capped_plans: list[TeamPlan] = []
    for plan in plans:
        capacity = prelim_capacity[plan.team_id]
        factor = 0.0 if capacity <= 0.0 else capped_capacity[plan.team_id] / capacity
        capped_plans.append(_scaled_plan(prelim[plan.team_id], factor))
    return tuple(capped_plans), capped_capacity


def _projection_requested_load_mwh(
    plan: TeamPlan,
    period_indices: tuple[int, ...],
    time_step_hours: float = 1.0,
) -> float:
    total = 0.0
    for connection in plan.connections:
        for period_index in period_indices:
            profile_value = (
                1.0
                if not connection.profile
                else connection.profile[period_index]
            )
            total += connection.capacity_mw * profile_value * time_step_hours
    return total


def _projection_utility(
    alphas: Sequence[float],
    requested_load_mwh_by_team: Mapping[str, float],
    team_ids: Sequence[str],
) -> float:
    utility = 0.0
    for index, team_id in enumerate(team_ids):
        if requested_load_mwh_by_team[team_id] <= 0.0:
            continue
        load = requested_load_mwh_by_team[team_id] * alphas[index]
        if load <= 0.0:
            return -math.inf
        utility += math.log(load)
    return utility


def _fair_continuous_projection(
    artifact_dir: str | Path,
    plans: tuple[TeamPlan, ...],
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...],
    *,
    projection_iterations: int = 10,
    time_step_hours: float = 1.0,
) -> FairProjectionResult | None:
    """Project valid plans continuously using total-load then PF objectives.

    Feasibility is supplied by the existing fixed-load DCOPF adapter.  The
    projection searches only continuous team fractions; no integer
    curtailment variables or team-removal ordering are introduced.  The
    optimizer relies on the adapter's coordinatewise downward-closed and
    convex feasibility region: multi-team cases use certified box bounds and
    two-team cases use a certified upper-boundary reduction.  Results are
    optimal to the explicit numeric tolerance; pathological black-box oracles
    can require exponential refinement.
    """
    if projection_iterations <= 0:
        raise ValueError("projection_iterations must be positive")
    if not math.isfinite(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    if not plans or not (Path(artifact_dir) / "bus.csv").is_file():
        return None

    capped_plans, capped_capacity = _projection_capped_plans(plans)
    team_ids = tuple(plan.team_id for plan in capped_plans)
    requested_loads = {
        plan.team_id: _projection_requested_load_mwh(
            plan,
            period_indices,
            time_step_hours=time_step_hours,
        )
        for plan in capped_plans
    }
    weights = requested_loads
    cache: dict[tuple[float, ...], tuple[BatchDispatchResult, tuple[TeamPlan, ...]] | None] = {}

    def evaluate(alphas: Sequence[float]):
        normalized = tuple(round(min(1.0, max(0.0, float(alpha))), 12) for alpha in alphas)
        if normalized in cache:
            return cache[normalized]
        projected_plans = tuple(
            _scaled_plan_to_cap(capped_plan, normalized[index])
            for index, capped_plan in enumerate(capped_plans)
        )
        try:
            dispatch = _dispatch_plans(
                artifact_dir,
                projected_plans,
                period_indices,
                source_period_indices=source_period_indices,
                stop_on_infeasible=True,
            )
        except (DCOPFInfeasible, DCOPFUnresolved, ValueError, NotImplementedError):
            result = None
        else:
            result = (dispatch, projected_plans)
        cache[normalized] = result
        return result

    def total_load(alphas: Sequence[float]) -> float:
        return sum(weights[team_id] * alphas[index] for index, team_id in enumerate(team_ids))

    zero = tuple(0.0 for _ in team_ids)
    zero_result = evaluate(zero)
    if zero_result is None:
        return None

    ray_iterations = max(projection_iterations, 32)

    def ray_maximum(
        lower: tuple[float, ...],
        upper: tuple[float, ...],
    ) -> tuple[tuple[float, ...], tuple[BatchDispatchResult, tuple[TeamPlan, ...]]] | None:
        """Find the feasible endpoint on a monotone segment by bisection."""
        lower_result = evaluate(lower)
        if lower_result is None:
            return None
        upper_result = evaluate(upper)
        if upper_result is not None:
            return upper, upper_result
        low = lower
        high = upper
        low_result = lower_result
        for _ in range(ray_iterations):
            midpoint = tuple((left + right) / 2.0 for left, right in zip(low, high))
            midpoint_result = evaluate(midpoint)
            if midpoint_result is None:
                high = midpoint
            else:
                low = midpoint
                low_result = midpoint_result
            if max(right - left for left, right in zip(low, high)) <= _FAIR_PROJECTION_TOLERANCE:
                break
        return low, low_result

    def run_box_search(
        *,
        initial_candidates: Sequence[
            tuple[tuple[float, ...], tuple[BatchDispatchResult, tuple[TeamPlan, ...]]]
        ],
        score,
        upper_bound,
        acceptable,
        tolerance,
        minimum_total: float | None = None,
    ):
        """Optimize a monotone objective with certified box upper bounds."""
        best_vector: tuple[float, ...] | None = None
        best_result: tuple[BatchDispatchResult, tuple[TeamPlan, ...]] | None = None
        best_score = -math.inf
        serial = 0
        queue: list[tuple[float, int, tuple[float, ...], tuple[float, ...]]] = []

        def update(vector, result) -> None:
            nonlocal best_vector, best_result, best_score
            if not acceptable(vector):
                return
            candidate_score = score(vector)
            if candidate_score > best_score + 1e-12 or (
                math.isclose(candidate_score, best_score, abs_tol=1e-12)
                and (best_vector is None or vector < best_vector)
            ):
                best_vector = tuple(vector)
                best_result = result
                best_score = candidate_score

        for vector, result in initial_candidates:
            update(vector, result)

        def inspect(lower, upper) -> None:
            nonlocal serial
            lower_result = evaluate(lower)
            if lower_result is None:
                return
            upper_total = total_load(upper)
            if minimum_total is not None and upper_total < minimum_total:
                return
            upper_result = evaluate(upper)
            if upper_result is not None:
                update(upper, upper_result)
                return
            ray_result = ray_maximum(lower, upper)
            if ray_result is not None:
                ray_vector, ray_dispatch = ray_result
                update(ray_vector, ray_dispatch)
            bound = upper_bound(upper)
            if bound <= best_score + tolerance:
                return
            serial += 1
            heapq.heappush(queue, (-bound, serial, tuple(lower), tuple(upper)))

        root_lower = zero
        root_upper = tuple(1.0 for _ in team_ids)
        inspect(root_lower, root_upper)
        while queue:
            negative_bound, _serial, lower, upper = heapq.heappop(queue)
            bound = -negative_bound
            if bound <= best_score + tolerance:
                break
            split_index = min(
                range(len(team_ids)),
                key=lambda index: (
                    -(upper[index] - lower[index]),
                    -weights[team_ids[index]],
                    team_ids[index],
                ),
            )
            midpoint = (lower[split_index] + upper[split_index]) / 2.0
            left_upper = list(upper)
            left_upper[split_index] = midpoint
            right_lower = list(lower)
            right_lower[split_index] = midpoint
            inspect(lower, tuple(left_upper))
            inspect(tuple(right_lower), upper)
        return best_vector, best_result

    def initial_rays():
        candidates = [(zero, zero_result)]
        for index in range(len(team_ids)):
            upper = [0.0] * len(team_ids)
            upper[index] = 1.0
            result = ray_maximum(zero, tuple(upper))
            if result is not None:
                candidates.append(result)
        common = ray_maximum(zero, tuple(1.0 for _ in team_ids))
        if common is not None:
            candidates.append(common)
        return candidates

    total_candidates = initial_rays()
    two_team_search = len(team_ids) == 2 and not any(
        plan.storages for plan in capped_plans
    )
    two_team_boundary_candidates: list[
        tuple[tuple[float, ...], tuple[BatchDispatchResult, tuple[TeamPlan, ...]]]
    ] = []

    def two_team_boundary(first_alpha: float):
        """Return the largest feasible second fraction at a fixed first fraction."""
        lower = (first_alpha, 0.0)
        lower_result = evaluate(lower)
        if lower_result is None:
            return None
        upper = (first_alpha, 1.0)
        upper_result = evaluate(upper)
        if upper_result is not None:
            return upper, upper_result
        low, high = 0.0, 1.0
        best = lower_result
        for _ in range(ray_iterations):
            midpoint = (low + high) / 2.0
            midpoint_result = evaluate((first_alpha, midpoint))
            if midpoint_result is None:
                high = midpoint
            else:
                low = midpoint
                best = midpoint_result
            if high - low <= _FAIR_PROJECTION_TOLERANCE:
                break
        return (first_alpha, low), best

    if two_team_search:
        first_axis = max(
            (vector[0] for vector, _result in total_candidates if vector[1] == 0.0),
            default=0.0,
        )
        lower_first, upper_first = 0.0, first_axis
        for endpoint in (lower_first, upper_first):
            boundary = two_team_boundary(endpoint)
            if boundary is not None:
                two_team_boundary_candidates.append(boundary)
        for _ in range(max(ray_iterations, 40)):
            left = lower_first + (upper_first - lower_first) / 3.0
            right = upper_first - (upper_first - lower_first) / 3.0
            left_boundary = two_team_boundary(left)
            right_boundary = two_team_boundary(right)
            if left_boundary is not None:
                two_team_boundary_candidates.append(left_boundary)
            if right_boundary is not None:
                two_team_boundary_candidates.append(right_boundary)
            left_value = (
                total_load(left_boundary[0]) if left_boundary is not None else -math.inf
            )
            right_value = (
                total_load(right_boundary[0]) if right_boundary is not None else -math.inf
            )
            if left_value < right_value:
                lower_first = left
            else:
                upper_first = right
            if upper_first - lower_first <= _FAIR_PROJECTION_TOLERANCE:
                break
        total_candidates.extend(two_team_boundary_candidates)
    has_positive_coordinate = any(
        vector[index] >= _FAIR_PROJECTION_MIN_SERVICE_FRACTION
        for vector, _result in total_candidates
        for index, team_id in enumerate(team_ids)
        if weights[team_id] > 0.0
    )
    if has_positive_coordinate and not two_team_search:
        best_vector, best_result = run_box_search(
            initial_candidates=total_candidates,
            score=total_load,
            upper_bound=total_load,
            acceptable=lambda _vector: True,
            tolerance=_FAIR_PROJECTION_TOTAL_LOAD_TOLERANCE,
        )
    elif has_positive_coordinate:
        best_vector, best_result = max(
            total_candidates,
            key=lambda item: (total_load(item[0]), tuple(-value for value in item[0])),
        )
    else:
        best_vector, best_result = zero, zero_result
    if best_vector is None or best_result is None:
        return None

    best_total = total_load(best_vector)
    total_tolerance = max(
        _FAIR_PROJECTION_TOTAL_LOAD_TOLERANCE,
        best_total * _FAIR_PROJECTION_TOTAL_LOAD_TOLERANCE,
    )
    total_optimal_floor = best_total - total_tolerance
    eligible_indices = tuple(
        index for index, team_id in enumerate(team_ids) if weights[team_id] > 0.0
    )

    def pf_acceptable(vector) -> bool:
        return (
            total_load(vector) >= total_optimal_floor
            and all(
                vector[index] >= _FAIR_PROJECTION_MIN_SERVICE_FRACTION
                for index in eligible_indices
            )
        )

    def pf_upper_bound(vector) -> float:
        if total_load(vector) < total_optimal_floor:
            return -math.inf
        if any(
            vector[index] < _FAIR_PROJECTION_MIN_SERVICE_FRACTION
            for index in eligible_indices
        ):
            return -math.inf
        return _projection_utility(vector, requested_loads, team_ids)

    if two_team_search:
        pf_vector = None
        pf_result = None
        pf_score = -math.inf
        for vector, result in total_candidates + [(best_vector, best_result)]:
            if not pf_acceptable(vector):
                continue
            score = _projection_utility(vector, requested_loads, team_ids)
            if score > pf_score:
                pf_vector, pf_result, pf_score = vector, result, score
        first_low = min(vector[0] for vector, _result in two_team_boundary_candidates)
        first_high = max(vector[0] for vector, _result in two_team_boundary_candidates)
        for _ in range(max(ray_iterations, 40)):
            left = first_low + (first_high - first_low) / 3.0
            right = first_high - (first_high - first_low) / 3.0
            left_boundary = two_team_boundary(left)
            right_boundary = two_team_boundary(right)
            left_score = (
                _projection_utility(left_boundary[0], requested_loads, team_ids)
                if left_boundary is not None and pf_acceptable(left_boundary[0])
                else -math.inf
            )
            right_score = (
                _projection_utility(right_boundary[0], requested_loads, team_ids)
                if right_boundary is not None and pf_acceptable(right_boundary[0])
                else -math.inf
            )
            for boundary in (left_boundary, right_boundary):
                if boundary is None:
                    continue
                vector, result = boundary
                if pf_acceptable(vector):
                    score = _projection_utility(vector, requested_loads, team_ids)
                    if score > pf_score:
                        pf_vector, pf_result, pf_score = vector, result, score
            if left_score < right_score:
                first_low = left
            else:
                first_high = right
            if first_high - first_low <= _FAIR_PROJECTION_TOLERANCE:
                break
        pf_candidates = (pf_vector, pf_result)
    else:
        pf_candidates = run_box_search(
            initial_candidates=total_candidates + [(best_vector, best_result)],
            score=lambda vector: _projection_utility(vector, requested_loads, team_ids),
            upper_bound=pf_upper_bound,
            acceptable=pf_acceptable,
            tolerance=_FAIR_PROJECTION_TOLERANCE,
            minimum_total=total_optimal_floor,
        )
    if pf_candidates[0] is not None and pf_candidates[1] is not None:
        best_vector, best_result = pf_candidates

    dispatch, projected_plans = best_result
    projected_capacity = {
        team_id: capped_capacity[team_id] * best_vector[index]
        for index, team_id in enumerate(team_ids)
    }
    projected_loads = {
        team_id: requested_loads[team_id] * best_vector[index]
        for index, team_id in enumerate(team_ids)
    }
    proportional_fair_utility = _projection_utility(best_vector, requested_loads, team_ids)
    diagnostics = ProjectionDiagnostics(
        service_fraction_by_team={team_id: best_vector[index] for index, team_id in enumerate(team_ids)},
        projected_capacity_mw_by_team=projected_capacity,
        capped_request_capacity_mw_by_team=dict(capped_capacity),
        requested_load_mwh_by_team=dict(requested_loads),
        projected_load_mwh_by_team=projected_loads,
        total_requested_load_mwh=sum(requested_loads.values()),
        total_projected_load_mwh=sum(projected_loads.values()),
        proportional_fair_utility=(
            proportional_fair_utility
        ),
        total_load_tolerance_mwh=total_tolerance,
        cap_policy={
            "site_capacity_mw": _FAIR_PROJECTION_SITE_CAPACITY_MW,
            "max_team_capacity_mw": _FAIR_PROJECTION_MAX_TEAM_CAPACITY_MW,
            "max_team_to_min_request_ratio": _FAIR_PROJECTION_MAX_TEAM_TO_MIN_REQUEST_RATIO,
            "minimum_positive_request_capacity_mw": min(
                (capacity for capacity in capped_capacity.values() if capacity > 0.0),
                default=0.0,
            ),
        },
    )
    try:
        dispatch_with_projection = replace(
            dispatch,
            service_fraction_by_team=diagnostics.service_fraction_by_team,
        )
    except TypeError:
        dispatch_with_projection = copy(dispatch)
        try:
            dispatch_with_projection.service_fraction_by_team = diagnostics.service_fraction_by_team
        except AttributeError:
            dispatch_with_projection = dispatch
    return FairProjectionResult(
        dispatch=dispatch_with_projection,
        service_fraction_by_team=diagnostics.service_fraction_by_team,
        projected_capacity_mw_by_team=projected_capacity,
        projected_plans=projected_plans,
        diagnostics=diagnostics,
    )


def _dispatch_plans(
    artifact_dir: str | Path,
    plans: Sequence[TeamPlan],
    period_indices: tuple[int, ...],
    *,
    source_period_indices: tuple[int, ...] | None = None,
    workers: int = 1,
    stop_on_infeasible: bool = False,
) -> BatchDispatchResult:
    """Call the adapter with optional performance controls.

    The compatibility fallback keeps lightweight test doubles and older
    adapters usable while production adapters receive the acceleration flags.
    """
    source_periods = (
        period_indices
        if source_period_indices is None
        else tuple(source_period_indices)
    )
    _validate_period_mapping(period_indices, source_periods)
    adapter_plans = _source_indexed_plans(
        plans,
        period_indices,
        source_periods,
    )
    optional = {}
    if workers != 1:
        optional["workers"] = workers
    if stop_on_infeasible:
        optional["stop_on_infeasible"] = True
    if not optional:
        dispatch = evaluate_plans_with_prepared_dcopf(
            artifact_dir, adapter_plans, period_indices=source_periods
        )
    else:
        try:
            dispatch = evaluate_plans_with_prepared_dcopf(
                artifact_dir, adapter_plans, period_indices=source_periods, **optional
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            dispatch = evaluate_plans_with_prepared_dcopf(
                artifact_dir, adapter_plans, period_indices=source_periods
            )
    return _relabel_dispatch_periods(dispatch, period_indices)


def _evaluation_plans(
    accepted: Sequence[SubmissionRecord],
    plan_overrides: Mapping[str, TeamPlan] | None,
) -> tuple[TeamPlan, ...]:
    parsed_by_team = {
        record.team_id: record.submission.plan
        for record in accepted
        if record.submission is not None
    }
    if plan_overrides is None:
        return tuple(parsed_by_team[team_id] for team_id in sorted(parsed_by_team))
    if not isinstance(plan_overrides, Mapping):
        raise ValueError("plan_overrides must be a mapping")
    unknown_teams = sorted(set(plan_overrides) - set(parsed_by_team))
    if unknown_teams:
        raise ValueError(
            "plan_overrides contains teams without accepted submissions: "
            + ", ".join(unknown_teams)
        )
    plans: dict[str, TeamPlan] = dict(parsed_by_team)
    for team_id, plan in plan_overrides.items():
        if not isinstance(plan, TeamPlan):
            raise ValueError(f"plan_overrides[{team_id!r}] must be a TeamPlan")
        if plan.team_id != team_id:
            raise ValueError(
                f"plan_overrides[{team_id!r}] has mismatched team_id {plan.team_id!r}"
            )
        plans[team_id] = plan
    return tuple(plans[team_id] for team_id in sorted(plans))


def _validate_capital_cost_scope(
    scope: CapitalCostScope | None,
    plans: Sequence[TeamPlan],
) -> None:
    if scope is None:
        return
    plan_by_team = {plan.team_id: plan for plan in plans}
    for team_id, asset_ids in scope.generator_ids_by_team.items():
        plan = plan_by_team.get(team_id)
        if plan is None:
            raise ValueError(f"capital cost scope contains unknown team {team_id!r}")
        known_ids = {generator.generator_id for generator in plan.generators}
        unknown_ids = sorted(asset_ids - known_ids)
        if unknown_ids:
            raise ValueError(
                f"capital cost scope contains unknown generator asset(s) for {team_id}: "
                + ", ".join(unknown_ids)
            )
    for team_id, asset_ids in scope.storage_ids_by_team.items():
        plan = plan_by_team.get(team_id)
        if plan is None:
            raise ValueError(f"capital cost scope contains unknown team {team_id!r}")
        known_ids = {storage.storage_id for storage in plan.storages}
        unknown_ids = sorted(asset_ids - known_ids)
        if unknown_ids:
            raise ValueError(
                f"capital cost scope contains unknown storage asset(s) for {team_id}: "
                + ", ".join(unknown_ids)
            )


def _validate_site_cost_scope(
    scope: Mapping[str, int] | None,
    plans: Sequence[TeamPlan],
) -> None:
    if scope is None:
        return
    if not isinstance(scope, Mapping):
        raise ValueError("site_cost_connection_count_by_team must be a mapping")
    known_teams = {plan.team_id for plan in plans}
    for team_id, connection_count in scope.items():
        if team_id not in known_teams:
            raise ValueError(
                f"site cost scope contains unknown team {team_id!r}"
            )
        if (
            not isinstance(connection_count, int)
            or isinstance(connection_count, bool)
            or connection_count < 0
        ):
            raise ValueError(
                "site cost connection counts must be non-negative integers"
            )


def _validate_site_cost_capacity_scope(
    scope: Mapping[str, float] | None,
    plans: Sequence[TeamPlan],
) -> None:
    if scope is None:
        return
    if not isinstance(scope, Mapping):
        raise ValueError("site_cost_connection_capacity_mw_by_team must be a mapping")
    known_teams = {plan.team_id for plan in plans}
    for team_id, capacity in scope.items():
        if team_id not in known_teams:
            raise ValueError(f"site cost scope contains unknown team {team_id!r}")
        if isinstance(capacity, bool) or not isinstance(capacity, (int, float)):
            raise ValueError("site cost connection capacities must be numeric")
        if not math.isfinite(float(capacity)) or capacity < 0:
            raise ValueError("site cost connection capacities must be finite and non-negative")


def evaluate_csv_batch(
    csv_paths: Sequence[str | Path] | str | Path,
    *,
    artifact_dir: str | Path,
    region_to_source_bus: Mapping[str, str] | None = None,
    bus_ids: set[str] | frozenset[str] | None = None,
    horizon: int,
    rules: BatchScoringRules,
    period_indices: tuple[int, ...] | None = None,
    source_period_indices: tuple[int, ...] | None = None,
    source_period_offset: int | None = None,
    default_generator_cost_per_mwh: float | None = None,
    dispatch_workers: int = 1,
    projection_iterations: int = 10,
    plan_overrides: Mapping[str, TeamPlan] | None = None,
    capital_cost_scope: CapitalCostScope | None = None,
    site_cost_connection_count_by_team: Mapping[str, int] | None = None,
    site_cost_connection_capacity_mw_by_team: Mapping[str, float] | None = None,
    submission_number: int | None = None,
) -> BatchEvaluation:
    """Collect and score valid submissions with deterministic failure isolation.

    ``plan_overrides`` changes only the evaluator-facing plans used for
    dispatch and operational scoring. CSV parsing, accepted/rejected records,
    and input hashes remain based on the submitted files. ``capital_cost_scope``
    optionally limits one-time generator/storage construction charges; when it
    is omitted, all assets in each evaluator-facing plan are charged. The
    optional site-capacity scope lets a multi-round coordinator charge only
    newly built connection MW; direct callers default to all plan connections.
    """
    generator_cost = (
        rules.default_generator_cost_per_mwh
        if default_generator_cost_per_mwh is None
        else default_generator_cost_per_mwh
    )
    selected_periods = tuple(range(horizon)) if period_indices is None else tuple(period_indices)
    if not isinstance(dispatch_workers, int) or isinstance(dispatch_workers, bool) or dispatch_workers <= 0:
        raise ValueError("dispatch_workers must be a positive integer")
    if not isinstance(projection_iterations, int) or isinstance(projection_iterations, bool) or projection_iterations <= 0:
        raise ValueError("projection_iterations must be a positive integer")
    if source_period_indices is not None and source_period_offset is not None:
        raise ValueError(
            "provide either source_period_indices or source_period_offset, not both"
        )
    _validate_rules(rules, horizon, selected_periods)
    if source_period_indices is None:
        offset = (
            _default_source_period_offset(horizon)
            if source_period_offset is None
            else source_period_offset
        )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("source_period_offset must be a non-negative integer")
        selected_source_periods = tuple(index + offset for index in selected_periods)
    else:
        selected_source_periods = tuple(source_period_indices)
    _validate_period_mapping(selected_periods, selected_source_periods)
    if bus_ids is None:
        try:
            bus_ids = load_bus_ids(artifact_dir)
        except (OSError, ValueError, csv.Error) as exc:
            raise ValueError(f"could not load production bus catalog: {exc}") from exc
    # Team-built generator IDs are evaluator-derived canonical keys and are
    # priced by the common classroom curve, not by the public generator catalog.
    generator_cost_by_id = None
    if rules.economic_profiles is not None:
        if rules.economic_profiles.horizon_hours != horizon:
            raise ValueError(
                "economic profile horizon must match the evaluation horizon"
            )
        profile_bus_ids = rules.economic_profiles.bus_ids
        if profile_bus_ids != frozenset(bus_ids):
            raise ValueError("economic profile bus set must match the production bus catalog")
    collection = _collect_evaluation_inputs(
        csv_paths,
        region_to_source_bus=region_to_source_bus,
        bus_ids=bus_ids,
        generator_cost_by_id=generator_cost_by_id,
        horizon=horizon,
        default_generator_cost_per_mwh=generator_cost,
        require_complete_horizon=True,
    )
    accepted = tuple(collection.accepted)
    plans = _expand_plans_for_horizon(
        _evaluation_plans(accepted, plan_overrides),
        horizon,
    )
    _validate_capital_cost_scope(capital_cost_scope, plans)
    _validate_site_cost_scope(site_cost_connection_count_by_team, plans)
    _validate_site_cost_capacity_scope(site_cost_connection_capacity_mw_by_team, plans)
    if submission_number not in (None, 1, 2):
        raise ValueError("submission_number must be 1, 2, or None")
    record_by_team = {record.team_id: record for record in accepted}
    all_records = tuple(
        sorted(
            (*collection.accepted, *collection.rejected),
            key=lambda record: _full_path_sort_key(record.path),
        )
    )
    input_hashes = {
        record.team_id: record.submission.sha256
        for record in accepted
        if record.submission is not None
    }
    input_hashes_by_submission = {
        record.submission_key: record.input_sha256
        for record in all_records
    }
    isolation_events: list[FailureIsolationEvent] = []
    dispatch_records: list[DispatchRecord] = []
    scores: list[TeamScore] = []
    dispatch: BatchDispatchResult | None = None
    projection_diagnostics: ProjectionDiagnostics | None = None
    batch_scope = "not_evaluated"
    batch_failure_reason: str | None = None

    if plans:
        attempted_ids = tuple(plan.team_id for plan in plans)
        attempted_submission_keys = tuple(record_by_team[team_id].submission_key for team_id in attempted_ids)
        try:
            dispatch = _dispatch_plans(
                artifact_dir,
                plans,
                selected_periods,
                source_period_indices=selected_source_periods,
                workers=dispatch_workers,
            )
        except (DCOPFInfeasible, DCOPFUnresolved) as exc:
            batch_failure_reason = str(exc)
            dispatch_records.append(
                DispatchRecord(
                    scope="joint",
                    attempted_team_ids=attempted_ids,
                    attempted_submission_keys=attempted_submission_keys,
                    status="infeasible",
                    failure_reason=str(exc),
                )
            )
            isolation_events.append(
                FailureIsolationEvent(
                    attempted_team_ids=attempted_ids,
                    removed_team_id=None,
                    outcome="joint_infeasible",
                    reason=str(exc),
                )
            )
            projection = _fair_continuous_projection(
                artifact_dir,
                plans,
                selected_periods,
                selected_source_periods,
                projection_iterations=projection_iterations,
                time_step_hours=rules.time_step_hours,
            )
            if projection is not None:
                dispatch = projection.dispatch
                projection_diagnostics = projection.diagnostics
                batch_scope = "joint_projection"
                dispatch_records.append(
                    DispatchRecord(
                        scope="joint_projection",
                        attempted_team_ids=attempted_ids,
                        attempted_submission_keys=attempted_submission_keys,
                        status="feasible",
                        dispatch=dispatch,
                    )
                )
                isolation_events.append(
                    FailureIsolationEvent(
                        attempted_team_ids=attempted_ids,
                        removed_team_id=None,
                        outcome="joint_projected",
                        reason=(
                            "continuous team service fractions="
                            + ",".join(
                                f"{team_id}:{fraction:.12g}"
                                for team_id, fraction in sorted(
                                    projection.service_fraction_by_team.items()
                                )
                            )
                            + "; service fraction="
                            f"{min(projection.service_fraction_by_team.values()):.12g}; "
                            "minimum positive service fraction="
                            f"{_FAIR_PROJECTION_MIN_SERVICE_FRACTION:.12g}; "
                            "bisection tolerance="
                            f"{_FAIR_PROJECTION_TOLERANCE:.12g}; "
                            "total projected load="
                            f"{projection.total_projected_load_mwh:.12g}"
                        ),
                    )
                )
                scores.extend(
                    replace(
                        _score_team(
                            original_plan,
                            dispatch,
                            rules,
                            submission_key=record_by_team[original_plan.team_id].submission_key,
                            capital_cost_scope=capital_cost_scope,
                            site_cost_connection_count_by_team=site_cost_connection_count_by_team,
                            site_cost_connection_capacity_mw_by_team=site_cost_connection_capacity_mw_by_team,
                            submission_number=submission_number,
                        ),
                        competition_scope="joint_projection",
                    )
                    for original_plan in plans
                )
                # Skip discrete team-removal isolation: all valid teams receive
                # the same joint projected dispatch with team-specific fractions.
                projection = None
                recovered = True
            else:
                recovered = False
            if recovered:
                # Empty the legacy removal loop while preserving the existing
                # control flow and audit records.
                attempted_ids = ()
            # Legacy order-dependent subset recovery is reachable only when
            # the fair projection has no feasible endpoint.  It is kept for
            # compatibility, but its records remain ``joint_subset`` or
            # ``individual_fallback`` and are never labeled as a projection.
            for removed_team_id in attempted_ids:
                remainder = tuple(plan for plan in plans if plan.team_id != removed_team_id)
                if not remainder:
                    dispatch_records.append(
                        DispatchRecord(
                            scope="joint_subset",
                            attempted_team_ids=(),
                            attempted_submission_keys=(),
                            status="not_run",
                            failure_reason="removing the only team leaves no dispatchable plans",
                        )
                    )
                    isolation_events.append(
                        FailureIsolationEvent(
                            attempted_team_ids=attempted_ids,
                            removed_team_id=removed_team_id,
                            outcome="empty_remainder",
                            reason="removing the only team leaves no dispatchable plans",
                        )
                    )
                    continue
                try:
                    remainder_dispatch = _dispatch_plans(
                        artifact_dir,
                        remainder,
                        selected_periods,
                        source_period_indices=selected_source_periods,
                        workers=dispatch_workers,
                    )
                except (DCOPFInfeasible, DCOPFUnresolved) as remainder_exc:
                    remainder_ids = tuple(plan.team_id for plan in remainder)
                    dispatch_records.append(
                        DispatchRecord(
                            scope="joint_subset",
                            attempted_team_ids=remainder_ids,
                            attempted_submission_keys=tuple(
                                record_by_team[team_id].submission_key for team_id in remainder_ids
                            ),
                            status="infeasible",
                            failure_reason=str(remainder_exc),
                        )
                    )
                    isolation_events.append(
                        FailureIsolationEvent(
                            attempted_team_ids=attempted_ids,
                            removed_team_id=removed_team_id,
                            outcome="infeasible_remainder",
                            reason=(
                                "legacy order-dependent team-removal fallback: "
                                f"remainder remained infeasible ({remainder_exc})"
                            ),
                        )
                    )
                    continue
                dispatch = remainder_dispatch
                remainder_ids = tuple(plan.team_id for plan in remainder)
                dispatch_records.append(
                    DispatchRecord(
                        scope="joint_subset",
                        attempted_team_ids=remainder_ids,
                        attempted_submission_keys=tuple(
                            record_by_team[team_id].submission_key for team_id in remainder_ids
                        ),
                        status="feasible",
                        dispatch=remainder_dispatch,
                    )
                )
                batch_scope = "joint"
                isolation_events.append(
                    FailureIsolationEvent(
                        attempted_team_ids=attempted_ids,
                        removed_team_id=removed_team_id,
                        outcome="feasible_remainder",
                        reason=(
                            "legacy order-dependent team-removal fallback: "
                            f"{batch_failure_reason}"
                        ),
                    )
                )
                scores.extend(
                    replace(
                        _score_team(
                            plan,
                            dispatch,
                            rules,
                            submission_key=record_by_team[plan.team_id].submission_key,
                            capital_cost_scope=capital_cost_scope,
                            site_cost_connection_count_by_team=site_cost_connection_count_by_team,
                            site_cost_connection_capacity_mw_by_team=site_cost_connection_capacity_mw_by_team,
                            submission_number=submission_number,
                        ),
                        competition_scope="joint",
                    )
                    for plan in remainder
                )
                scores.append(
                    _infeasible_score(
                        removed_team_id,
                        rules,
                        valid=True,
                        reason=batch_failure_reason,
                        submission_key=record_by_team[removed_team_id].submission_key,
                    )
                )
                recovered = True
                break

            if not recovered:
                batch_scope = "individual_fallback"
                isolation_events.append(
                FailureIsolationEvent(
                    attempted_team_ids=attempted_ids,
                    removed_team_id=None,
                    outcome="individual_fallback",
                    reason=(
                        "fair continuous projection unavailable; "
                        f"{batch_failure_reason}"
                    ),
                )
            )
                for plan in plans:
                    submission_key = record_by_team[plan.team_id].submission_key
                    try:
                        individual_dispatch = _dispatch_plans(
                            artifact_dir,
                            (plan,),
                            selected_periods,
                            source_period_indices=selected_source_periods,
                            workers=dispatch_workers,
                        )
                    except (DCOPFInfeasible, DCOPFUnresolved) as individual_exc:
                        dispatch_records.append(
                            DispatchRecord(
                                scope="individual_fallback",
                                attempted_team_ids=(plan.team_id,),
                                attempted_submission_keys=(submission_key,),
                                status="infeasible",
                                failure_reason=str(individual_exc),
                            )
                        )
                        scores.append(
                            _infeasible_score(
                                plan.team_id,
                                rules,
                                valid=True,
                                reason=str(individual_exc),
                                competition_scope="individual_fallback",
                                submission_key=submission_key,
                            )
                        )
                    else:
                        dispatch_records.append(
                            DispatchRecord(
                                scope="individual_fallback",
                                attempted_team_ids=(plan.team_id,),
                                attempted_submission_keys=(submission_key,),
                                status="feasible",
                                dispatch=individual_dispatch,
                            )
                        )
                        scores.append(
                            replace(
                                _score_team(
                                    plan,
                                    individual_dispatch,
                                    rules,
                                    submission_key=submission_key,
                                    capital_cost_scope=capital_cost_scope,
                                    site_cost_connection_count_by_team=site_cost_connection_count_by_team,
                                    site_cost_connection_capacity_mw_by_team=site_cost_connection_capacity_mw_by_team,
                                    submission_number=submission_number,
                                ),
                                competition_scope="individual_fallback",
                            )
                        )
        else:
            batch_scope = "joint"
            dispatch_records.append(
                DispatchRecord(
                    scope="joint",
                    attempted_team_ids=attempted_ids,
                    attempted_submission_keys=attempted_submission_keys,
                    status="feasible",
                    dispatch=dispatch,
                )
            )
            isolation_events.append(
                FailureIsolationEvent(
                    attempted_team_ids=attempted_ids,
                    removed_team_id=None,
                    outcome="joint_feasible",
                    reason="joint dispatch feasible",
                )
            )
            scores.extend(
                replace(
                    _score_team(
                        plan,
                        dispatch,
                        rules,
                        submission_key=record_by_team[plan.team_id].submission_key,
                        capital_cost_scope=capital_cost_scope,
                        site_cost_connection_count_by_team=site_cost_connection_count_by_team,
                        site_cost_connection_capacity_mw_by_team=site_cost_connection_capacity_mw_by_team,
                        submission_number=submission_number,
                    ),
                    competition_scope="joint",
                )
                for plan in plans
            )
    else:
        dispatch_records.append(
            DispatchRecord(
                scope="not_evaluated",
                attempted_team_ids=(),
                attempted_submission_keys=(),
                status="not_run",
                failure_reason="no valid submissions",
            )
        )

    scores.extend(
        _infeasible_score(
            record.team_id,
            rules,
            valid=False,
            reason=record.failure_reason or "submission was rejected",
            submission_key=record.submission_key,
        )
        for record in collection.rejected
    )
    ordered = sorted(
        scores,
        key=lambda item: (
            0 if item.valid and item.feasible else 1 if item.valid else 2,
            -item.score,
            item.team_id,
        ),
    )
    ranked = tuple(
        replace(score, rank=rank)
        for rank, score in enumerate(ordered, start=1)
    )
    return BatchEvaluation(
        rules_version=rules.rules_version,
        competition_scope=batch_scope,
        input_sha256_by_team=input_hashes,
        team_scores=ranked,
        rejected_submissions=collection.rejected,
        isolation_events=tuple(isolation_events),
        dispatch=dispatch,
        failure_reason=batch_failure_reason,
        rules_payload={
            "rules_version": rules.rules_version,
            "it_value_per_mwh": list(rules.it_value_per_mwh),
            "energy_price_per_mwh": list(rules.energy_price_per_mwh),
            "time_step_hours": rules.time_step_hours,
            "generator_capital_cost_per_mw": rules.generator_capital_cost_per_mw,
            "site_cost_per_mw": rules.site_cost_per_mw,
            "site_cost_per_site": rules.site_cost_per_site,
            "infeasible_score": rules.infeasible_score,
            "default_generator_cost_per_mwh": generator_cost,
            "storage_capital_cost_per_mw": rules.storage_capital_cost_per_mw,
            "storage_capital_cost_per_mwh": rules.storage_capital_cost_per_mwh,
            "submission_number": submission_number,
            **(
                {
                    "economic_profile": {
                        "profile_version": rules.economic_profiles.profile_version,
                        "horizon_hours": rules.economic_profiles.horizon_hours,
                        "currency_year": rules.economic_profiles.currency_year,
                        "price_source": rules.economic_profiles.price_source,
                        "bus_count": len(rules.economic_profiles.bus_ids),
                    }
                }
                if rules.economic_profiles is not None
                else {}
            ),
        },
        accepted_submissions=accepted,
        input_sha256_by_submission=input_hashes_by_submission,
        dispatch_records=tuple(dispatch_records),
        source_period_indices=selected_source_periods,
        projection_diagnostics=projection_diagnostics,
    )


_LEADERBOARD_FIELDS = (
    "team_id",
    "rank",
    "valid",
    "feasible",
    "score",
    "served_load_mwh",
    "it_value",
    "energy_cost",
    "generator_operating_cost",
    "generator_capital_cost",
    "site_cost",
    "storage_capital_cost",
    "competition_scope",
    "failure_reason",
)
_SUBMISSION_MANIFEST_FIELDS = (
    "submission_key",
    "team_id",
    "submission_id",
    "source_filename",
    "input_sha256",
    "valid",
    "failure_reason",
    "identity_is_fallback",
)


def _canonical_json_bytes(payload: Any) -> bytes:
    def json_safe(value: Any) -> Any:
        if isinstance(value, float):
            if math.isnan(value):
                return "nan"
            if math.isinf(value):
                return "-inf" if value < 0 else "inf"
            return value
        if isinstance(value, Mapping):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    return (
        json.dumps(
            json_safe(payload),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_canonical_json(path: Path, payload: Any) -> None:
    path.write_bytes(_canonical_json_bytes(payload))


def _sorted_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    return {str(key): payload[key] for key in sorted(payload, key=str)}


def _serialized_submission_record(
    record: SubmissionRecord,
    input_sha256_by_submission: Mapping[str, str | None],
) -> dict[str, Any]:
    return {
        "submission_key": record.submission_key,
        "team_id": record.team_id,
        "submission_id": record.submission_id,
        "source_filename": record.path.name,
        "input_sha256": input_sha256_by_submission.get(record.submission_key, record.input_sha256),
        "valid": record.valid,
        "failure_reason": record.failure_reason,
        "identity_is_fallback": record.identity_is_fallback,
    }


def _serialized_period(period: Any) -> dict[str, Any]:
    dcopf = period.dcopf
    flow_by_branch = _sorted_mapping(getattr(dcopf, "flow_mw_by_branch", None))
    balance_dual_by_bus = _sorted_mapping(getattr(dcopf, "balance_dual_by_bus", None))
    binding_diagnostics = getattr(dcopf, "binding_diagnostics", None)
    if binding_diagnostics is None:
        binding_diagnostics = {}
    elif not isinstance(binding_diagnostics, Mapping):
        binding_diagnostics = {"available": binding_diagnostics}
    return {
        "period_index": period.period_index,
        "solver_status": dcopf.status,
        "objective_cost": dcopf.objective_cost,
        "submitted_load_mw_by_team": _sorted_mapping(period.submitted_load_mw_by_team),
        "generator_dispatch_mw_by_team": {
            str(team_id): _sorted_mapping(generator_dispatch)
            for team_id, generator_dispatch in sorted(
                period.generator_dispatch_mw_by_team.items(), key=lambda item: str(item[0])
            )
        },
        "grid_purchase_mw_by_team": _sorted_mapping(getattr(period, "grid_purchase_mw_by_team", None)),
        "storage_charge_mw_by_team": {
            str(team_id): _sorted_mapping(values)
            for team_id, values in sorted((getattr(period, "storage_charge_mw_by_team", None) or {}).items(), key=lambda item: str(item[0]))
        },
        "storage_discharge_mw_by_team": {
            str(team_id): _sorted_mapping(values)
            for team_id, values in sorted((getattr(period, "storage_discharge_mw_by_team", None) or {}).items(), key=lambda item: str(item[0]))
        },
        "storage_soc_mwh_by_team": {
            str(team_id): _sorted_mapping(values)
            for team_id, values in sorted((getattr(period, "storage_soc_mwh_by_team", None) or {}).items(), key=lambda item: str(item[0]))
        },
        "generation_mw": _sorted_mapping(getattr(dcopf, "generation_mw", None)),
        "flow_mw_by_branch": flow_by_branch,
        "balance_dual_by_bus": balance_dual_by_bus,
        "binding_diagnostics": {
            **binding_diagnostics,
            "flow_mw_by_branch": flow_by_branch,
            "balance_dual_by_bus": balance_dual_by_bus,
        },
    }


def _serialized_dispatch_record(record: DispatchRecord) -> dict[str, Any]:
    periods = () if record.dispatch is None else record.dispatch.periods
    return {
        "scope": record.scope,
        "attempted_team_ids": list(record.attempted_team_ids),
        "attempted_submission_keys": list(record.attempted_submission_keys),
        "status": record.status,
        "failure_reason": record.failure_reason,
        "final_storage_soc_mwh_by_team": (
            {}
            if record.dispatch is None
            else getattr(record.dispatch, "final_storage_soc_mwh_by_team", None)
        ),
        "periods": [
            {
                "period_index": period.period_index,
                "solver_status": period.dcopf.status,
                "objective_cost": period.dcopf.objective_cost,
            }
            for period in periods
        ],
    }


def _period_audit_payloads(
    evaluation: BatchEvaluation,
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...] | None = None,
) -> dict[int, dict[str, Any]]:
    dispatch_records = [record for record in evaluation.dispatch_records if record.dispatch is not None]
    if not dispatch_records and evaluation.dispatch is not None:
        dispatch_records = [
            DispatchRecord(
                scope=evaluation.competition_scope,
                attempted_team_ids=tuple(evaluation.dispatch.team_ids),
                attempted_submission_keys=(),
                status="feasible",
                dispatch=evaluation.dispatch,
            )
        ]

    source_by_local = dict(
        zip(
            period_indices,
            period_indices
            if source_period_indices is None
            else source_period_indices,
        )
    )
    periods_by_index: dict[int, list[dict[str, Any]]] = {}
    for record in dispatch_records:
        assert record.dispatch is not None
        for period in record.dispatch.periods:
            periods_by_index.setdefault(period.period_index, []).append(_serialized_period(period))

    payloads: dict[int, dict[str, Any]] = {}
    for period_index in period_indices:
        entries = periods_by_index.get(period_index, [])
        if not entries:
            payloads[period_index] = {
                "period_index": period_index,
                "source_period_index": source_by_local.get(period_index, period_index),
                "solver_status": "not_run",
                "objective_cost": None,
                "submitted_load_mw_by_team": {},
                "generator_dispatch_mw_by_team": {},
                "generation_mw": {},
                "flow_mw_by_branch": {},
                "balance_dual_by_bus": {},
                "binding_diagnostics": {
                    "status": "not_run",
                    "reason": "no dispatch result was available",
                },
            }
            continue
        if len(entries) == 1:
            payloads[period_index] = {
                **entries[0],
                "source_period_index": source_by_local.get(period_index, period_index),
            }
            continue

        submitted_load: dict[str, float] = {}
        generator_dispatch: dict[str, dict[str, float]] = {}
        for entry in entries:
            for team_id, amount in entry["submitted_load_mw_by_team"].items():
                submitted_load[team_id] = submitted_load.get(team_id, 0.0) + amount
            for team_id, generators in entry["generator_dispatch_mw_by_team"].items():
                destination = generator_dispatch.setdefault(team_id, {})
                for generator_id, amount in generators.items():
                    destination[generator_id] = destination.get(generator_id, 0.0) + amount
        statuses = [str(entry["solver_status"]) for entry in entries]
        objectives = [entry["objective_cost"] for entry in entries]
        payloads[period_index] = {
            "period_index": period_index,
            "source_period_index": source_by_local.get(period_index, period_index),
            "solver_status": statuses[0] if len(set(statuses)) == 1 else "mixed",
            "objective_cost": sum(objectives) if all(value is not None for value in objectives) else None,
            "submitted_load_mw_by_team": _sorted_mapping(submitted_load),
            "generator_dispatch_mw_by_team": {
                team_id: _sorted_mapping(values)
                for team_id, values in sorted(generator_dispatch.items())
            },
            "generation_mw": {},
            "flow_mw_by_branch": {},
            "balance_dual_by_bus": {},
            "binding_diagnostics": {
                "dispatch_count": len(entries),
                "solver_statuses": statuses,
                "objective_costs": objectives,
                "source_period_diagnostics": [entry["binding_diagnostics"] for entry in entries],
            },
        }
    return payloads


def _aggregate_diagnostics(
    period_payloads: Mapping[int, Mapping[str, Any]],
    dispatch_records: Sequence[DispatchRecord],
) -> dict[str, Any]:
    objective_cost_by_period = {
        str(period_index): payload["objective_cost"]
        for period_index, payload in sorted(period_payloads.items())
    }
    available_objectives = [value for value in objective_cost_by_period.values() if value is not None]
    return {
        "dispatch_count": sum(record.dispatch is not None for record in dispatch_records),
        "period_count": len(period_payloads),
        "solver_status_by_period": {
            str(period_index): payload["solver_status"]
            for period_index, payload in sorted(period_payloads.items())
        },
        "objective_cost_by_period": objective_cost_by_period,
        "total_objective_cost": sum(available_objectives) if len(available_objectives) == len(objective_cost_by_period) else None,
        "binding_diagnostics_by_period": {
            str(period_index): payload["binding_diagnostics"]
            for period_index, payload in sorted(period_payloads.items())
        },
    }


def _safe_output_component(value: str) -> str:
    component = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return component or "submission"


_DIAGNOSTIC_STEM_MAX_BYTES = 96
_DIAGNOSTIC_HASH_LENGTH = 12


def _truncate_output_component(value: str, max_bytes: int) -> str:
    truncated = value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return truncated or "submission"


def _bounded_diagnostic_stem(record: SubmissionRecord) -> str:
    safe_component = _safe_output_component(record.team_id)
    if safe_component == record.team_id and len(safe_component.encode("utf-8")) <= _DIAGNOSTIC_STEM_MAX_BYTES:
        return safe_component
    identity = f"{record.team_id}\0{record.submission_id or ''}"
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_DIAGNOSTIC_HASH_LENGTH]
    return f"{_truncate_output_component(safe_component, _DIAGNOSTIC_STEM_MAX_BYTES)}--{suffix}"


def _diagnostic_filename_with_suffix(stem: str, suffix: str) -> str:
    bounded_stem = _truncate_output_component(stem, _DIAGNOSTIC_STEM_MAX_BYTES)
    return f"{bounded_stem}--{suffix[:_DIAGNOSTIC_HASH_LENGTH]}.json"


def _diagnostic_filenames(records: Sequence[SubmissionRecord]) -> dict[str, str]:
    team_counts = Counter(record.team_id for record in records)
    names: dict[str, str] = {}
    used: set[str] = set()
    for record in sorted(records, key=lambda item: item.submission_key):
        base = _bounded_diagnostic_stem(record)
        filename = f"{base}.json"
        if team_counts[record.team_id] > 1 or filename in used:
            suffix = hashlib.sha256(record.submission_key.encode("utf-8")).hexdigest()[:12]
            filename = _diagnostic_filename_with_suffix(base, suffix)
        while filename in used:
            suffix = hashlib.sha256((record.submission_key + filename).encode("utf-8")).hexdigest()[:12]
            filename = _diagnostic_filename_with_suffix(base, suffix)
        names[record.submission_key] = filename
        used.add(filename)
    return names


def _serialized_team_diagnostic(
    evaluation: BatchEvaluation,
    score: TeamScore,
    input_sha256_by_submission: Mapping[str, str | None],
    periods_by_team: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    submission = next(
        (
            record
            for record in (*evaluation.accepted_submissions, *evaluation.rejected_submissions)
            if record.submission_key == score.submission_key
        ),
        None,
    )
    return {
        "team_score": asdict(score),
        "projection": (
            None
            if evaluation.projection_diagnostics is None
            else {
                "service_fraction": evaluation.projection_diagnostics.service_fraction_by_team.get(score.team_id),
                "projected_capacity_mw": evaluation.projection_diagnostics.projected_capacity_mw_by_team.get(score.team_id),
                "capped_request_capacity_mw": evaluation.projection_diagnostics.capped_request_capacity_mw_by_team.get(score.team_id),
                "requested_load_mwh": evaluation.projection_diagnostics.requested_load_mwh_by_team.get(score.team_id),
                "projected_load_mwh": evaluation.projection_diagnostics.projected_load_mwh_by_team.get(score.team_id),
            }
        ),
        "submission_key": score.submission_key,
        "input_sha256": input_sha256_by_submission.get(score.submission_key),
        "submission": (
            _serialized_submission_record(submission, input_sha256_by_submission)
            if submission is not None
            else None
        ),
        "periods": periods_by_team.get(score.team_id, []),
        "rejected": next(
            (
                _serialized_submission_record(record, input_sha256_by_submission)
                for record in evaluation.rejected_submissions
                if record.submission_key == score.submission_key
            ),
            None,
        ),
    }


def _output_hashes(root: Path, generated_paths: Sequence[Path] | None = None) -> dict[str, str]:
    files = (
        list(generated_paths)
        if generated_paths is not None
        else [
            path
            for path in root.rglob("*")
            if path.is_file() and path.name not in {"evaluation.json", "evaluation_sha256.txt"}
        ]
    )
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files), key=lambda path: path.relative_to(root).as_posix())
    }


def _tracked_generated_paths(root: Path) -> tuple[Path, ...]:
    """Return paths explicitly marked as generated by a prior writer run."""
    evaluation_path = root / "evaluation.json"
    try:
        payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, Mapping):
        return ()
    metadata = payload.get("evaluation_metadata")
    if not isinstance(metadata, Mapping):
        return ()
    marked_paths = metadata.get("generated_artifacts")
    if not isinstance(marked_paths, list):
        return ()

    tracked: list[Path] = []
    for raw_path in marked_paths:
        if not isinstance(raw_path, str) or not raw_path:
            continue
        relative_path = Path(raw_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        candidate = root / relative_path
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        tracked.append(candidate)
    return tuple(tracked)


def _remove_tracked_generated_files(root: Path) -> None:
    for path in _tracked_generated_paths(root):
        if path.is_file():
            path.unlink()


_CLASSROOM_OUTPUT_FILES = (
    "leaderboard.csv",
    "submission_manifest.csv",
    "evaluation.json",
    "evaluation_sha256.txt",
)
_CLASSROOM_OUTPUT_DIRECTORIES = (
    "dispatch",
    "diagnostics",
    "shared",
    "teams",
)


def _remove_known_output_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _clear_classroom_output_namespace(root: Path) -> None:
    for relative_path in _CLASSROOM_OUTPUT_FILES + _CLASSROOM_OUTPUT_DIRECTORIES:
        _remove_known_output_path(root / relative_path)


def write_batch_outputs(
    evaluation: BatchEvaluation,
    output_dir: str | Path,
    *,
    scenario_version: str,
    evaluator_version: str,
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...] | None = None,
) -> None:
    """Write deterministic, self-auditing batch evaluation artifacts."""
    if not period_indices:
        raise ValueError("period_indices must be non-empty")
    if any(not isinstance(index, int) or isinstance(index, bool) for index in period_indices):
        raise ValueError("period_indices must contain integers")
    if len(set(period_indices)) != len(period_indices):
        raise ValueError("period_indices must not contain duplicates")
    if source_period_indices is None:
        if len(evaluation.source_period_indices) == len(period_indices):
            source_period_indices = evaluation.source_period_indices
        else:
            source_period_indices = period_indices
    source_period_indices = tuple(source_period_indices)
    _validate_period_mapping(period_indices, source_period_indices)

    root = Path(output_dir)
    diagnostics_dir = root / "diagnostics"
    dispatch_dir = root / "dispatch"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    _remove_tracked_generated_files(root)

    generated_paths: list[Path] = [root / "leaderboard.csv", root / "submission_manifest.csv"]

    leaderboard_rows = [
        {field: getattr(score, field) for field in _LEADERBOARD_FIELDS}
        for score in evaluation.team_scores
    ]
    with (root / "leaderboard.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=_LEADERBOARD_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(leaderboard_rows)

    records = tuple(
        sorted(
            (*evaluation.accepted_submissions, *evaluation.rejected_submissions),
            key=lambda record: record.submission_key,
        )
    )
    score_by_submission = {
        score.submission_key: score
        for score in evaluation.team_scores
        if score.submission_key is not None
    }
    manifest_rows = []
    for record in records:
        score = score_by_submission.get(record.submission_key)
        manifest_rows.append(
            {
                "submission_key": record.submission_key,
                "team_id": record.team_id,
                "submission_id": record.submission_id,
                "source_filename": record.path.name,
                "input_sha256": evaluation.input_sha256_by_submission.get(
                    record.submission_key, record.input_sha256
                ),
                "valid": record.valid,
                "failure_reason": (
                    score.failure_reason
                    if score is not None and score.failure_reason is not None
                    else record.failure_reason
                ),
                "identity_is_fallback": record.identity_is_fallback,
            }
        )
    with (root / "submission_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=_SUBMISSION_MANIFEST_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    selected_periods = _period_audit_payloads(
        evaluation,
        period_indices,
        source_period_indices,
    )
    for period_index in period_indices:
        dispatch_path = dispatch_dir / f"period_{period_index}.json"
        _write_canonical_json(dispatch_path, selected_periods[period_index])
        generated_paths.append(dispatch_path)

    periods_by_team: dict[str, list[dict[str, Any]]] = {score.team_id: [] for score in evaluation.team_scores}
    for period_index in period_indices:
        period = selected_periods[period_index]
        for team_id, load in period["submitted_load_mw_by_team"].items():
            periods_by_team.setdefault(team_id, []).append(
                {
                    "period_index": period_index,
                    "submitted_load_mw": load,
                    "generator_dispatch_mw": period["generator_dispatch_mw_by_team"].get(team_id, {}),
                }
            )
    diagnostic_filenames = _diagnostic_filenames(records)
    team_diagnostics = {}
    for score in evaluation.team_scores:
        diagnostic = _serialized_team_diagnostic(
            evaluation,
            score,
            evaluation.input_sha256_by_submission,
            periods_by_team,
        )
        record_key = score.submission_key or f"team:{score.team_id}"
        diagnostic_key = diagnostic_filenames.get(
            record_key,
            f"{_truncate_output_component(_safe_output_component(score.team_id), _DIAGNOSTIC_STEM_MAX_BYTES)}.json",
        )
        team_diagnostics[diagnostic_key.removesuffix(".json")] = diagnostic
        diagnostic_path = diagnostics_dir / diagnostic_key
        _write_canonical_json(
            diagnostic_path,
            diagnostic,
        )
        generated_paths.append(diagnostic_path)

    output_hashes = _output_hashes(root, generated_paths)
    aggregate_diagnostics = _aggregate_diagnostics(selected_periods, evaluation.dispatch_records)
    serialized_dispatch_records = [
        _serialized_dispatch_record(record) for record in evaluation.dispatch_records
    ]
    serialized_accepted = [
        _serialized_submission_record(record, evaluation.input_sha256_by_submission)
        for record in evaluation.accepted_submissions
    ]
    serialized_rejected = [
        _serialized_submission_record(record, evaluation.input_sha256_by_submission)
        for record in evaluation.rejected_submissions
    ]
    generated_artifacts = sorted(
        path.relative_to(root).as_posix()
        for path in generated_paths
    )
    scoring_configuration = evaluation.rules_payload or {"rules_version": evaluation.rules_version}
    evaluation_metadata = {
        "competition_scope": evaluation.competition_scope,
        "projection_diagnostics": (
            None
            if evaluation.projection_diagnostics is None
            else asdict(evaluation.projection_diagnostics)
        ),
        "period_indices": list(period_indices),
        "source_period_indices": list(source_period_indices),
        "accepted_submission_keys": [record.submission_key for record in evaluation.accepted_submissions],
        "rejected_submission_keys": [record.submission_key for record in evaluation.rejected_submissions],
        "scoring_configuration": scoring_configuration,
        "generated_artifacts": generated_artifacts,
        "subset_evaluations": serialized_dispatch_records,
        "failure_isolation": {
            "events": [asdict(event) for event in evaluation.isolation_events],
            "failure_reason": evaluation.failure_reason,
        },
    }
    run_seed = {
        "rules": evaluation.rules_payload or {"rules_version": evaluation.rules_version},
        "scoring_configuration": scoring_configuration,
        "rules_version": evaluation.rules_version,
        "scenario_version": scenario_version,
        "evaluator_version": evaluator_version,
        "period_indices": list(period_indices),
        "source_period_indices": list(source_period_indices),
        "input_sha256_by_team": _sorted_mapping(evaluation.input_sha256_by_team),
        "input_sha256_by_submission": _sorted_mapping(evaluation.input_sha256_by_submission),
        "competition_scope": evaluation.competition_scope,
        "projection_diagnostics": (
            None
            if evaluation.projection_diagnostics is None
            else asdict(evaluation.projection_diagnostics)
        ),
        "team_scores": [asdict(score) for score in evaluation.team_scores],
        "accepted_submissions": serialized_accepted,
        "rejected_submissions": serialized_rejected,
        "dispatch_records": serialized_dispatch_records,
        "evaluation_metadata": evaluation_metadata,
        "aggregate_diagnostics": aggregate_diagnostics,
        "isolation_events": [asdict(event) for event in evaluation.isolation_events],
        "periods": [selected_periods[index] for index in period_indices],
    }
    run_id = hashlib.sha256(_canonical_json_bytes(run_seed)).hexdigest()
    payload = {
        "run_id": run_id,
        "rules_version": evaluation.rules_version,
        "scenario_version": scenario_version,
        "evaluator_version": evaluator_version,
        "period_indices": list(period_indices),
        "source_period_indices": list(source_period_indices),
        "input_sha256_by_team": _sorted_mapping(evaluation.input_sha256_by_team),
        "input_sha256_by_submission": _sorted_mapping(evaluation.input_sha256_by_submission),
        "competition_scope": evaluation.competition_scope,
        "projection_diagnostics": (
            None
            if evaluation.projection_diagnostics is None
            else asdict(evaluation.projection_diagnostics)
        ),
        "isolation_events": [asdict(event) for event in evaluation.isolation_events],
        "team_scores": [asdict(score) for score in evaluation.team_scores],
        "accepted_submissions": serialized_accepted,
        "rejected_submissions": serialized_rejected,
        "dispatch_records": serialized_dispatch_records,
        "evaluation_metadata": evaluation_metadata,
        "aggregate_diagnostics": aggregate_diagnostics,
        "failure_reason": evaluation.failure_reason,
        "team_diagnostics": team_diagnostics,
        "periods": [selected_periods[index] for index in period_indices],
        "output_sha256": output_hashes,
        "rules": evaluation.rules_payload or {"rules_version": evaluation.rules_version},
    }
    _write_canonical_json(root / "evaluation.json", payload)
    evaluation_hash = hashlib.sha256((root / "evaluation.json").read_bytes()).hexdigest()
    (root / "evaluation_sha256.txt").write_text(evaluation_hash + "\n", encoding="utf-8")


def write_classroom_outputs(
    evaluation: BatchEvaluation,
    output_dir: str | Path,
    *,
    week_id: str,
    submission_number: int,
    feedback_only: bool,
    scored: bool,
    scenario_version: str,
    evaluator_version: str,
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...] | None = None,
) -> None:
    """Write aggregate classroom artifacts and isolated team summaries.

    This writer intentionally does not call or reuse the raw audit serializer.
    Every shared field is scalar or aggregate-only, and every team file is
    built from one ``TeamScore`` plus that team's scalar diagnostics.
    """
    if not period_indices:
        raise ValueError("period_indices must be non-empty")
    if source_period_indices is None:
        source_period_indices = evaluation.source_period_indices or period_indices
    source_period_indices = tuple(source_period_indices)
    _validate_period_mapping(tuple(period_indices), source_period_indices)

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _clear_classroom_output_namespace(root)
    shared_dir = root / "shared"
    teams_dir = root / "teams"
    shared_dir.mkdir(parents=True, exist_ok=True)
    teams_dir.mkdir(parents=True, exist_ok=True)

    selected_periods = _period_audit_payloads(
        evaluation,
        tuple(period_indices),
        source_period_indices,
    )
    aggregate_periods = [
        _classroom_aggregate_period(selected_periods[index])
        for index in period_indices
    ]
    scores = tuple(evaluation.team_scores)
    shared_summary = {
        "schema_version": "classroom-safe-v1",
        "week_id": week_id,
        "submission_number": submission_number,
        "feedback_only": feedback_only,
        "scored": scored,
        "scenario_version": scenario_version,
        "evaluator_version": evaluator_version,
        "competition_scope": evaluation.competition_scope,
        "team_count": len(scores),
        "valid_team_count": sum(score.valid for score in scores),
        "feasible_team_count": sum(score.valid and score.feasible for score in scores),
        "period_count": len(aggregate_periods),
        "projection": _classroom_aggregate_projection(evaluation.projection_diagnostics),
    }
    shared_paths = [shared_dir / "summary.json", shared_dir / "aggregate_periods.json"]
    _write_canonical_json(shared_paths[0], shared_summary)
    _write_canonical_json(shared_paths[1], {"periods": aggregate_periods})

    for score in scores:
        team_dir = teams_dir / f"team-{hashlib.sha256(score.team_id.encode('utf-8')).hexdigest()[:16]}"
        team_dir.mkdir(parents=True, exist_ok=True)
        _write_canonical_json(
            team_dir / "summary.json",
            _classroom_team_summary(
                evaluation,
                score,
                week_id=week_id,
                submission_number=submission_number,
                feedback_only=feedback_only,
                scored=scored,
                all_team_ids={item.team_id for item in scores},
            ),
        )

    generated_paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "output_sha256.json"
    )
    output_hashes = _output_hashes(root, generated_paths)
    _write_canonical_json(shared_dir / "output_sha256.json", output_hashes)


def _classroom_aggregate_period(period: Mapping[str, Any]) -> dict[str, Any]:
    def total_values(values: Mapping[str, Any]) -> float:
        return sum(float(value) for value in values.values())

    def total_nested_values(values: Mapping[str, Any]) -> float:
        return sum(total_values(nested) for nested in values.values() if isinstance(nested, Mapping))

    flow_values = period.get("flow_mw_by_branch", {})
    dual_values = period.get("balance_dual_by_bus", {})
    return {
        "period_index": period.get("period_index"),
        "source_period_index": period.get("source_period_index"),
        "solver_status": period.get("solver_status"),
        "objective_cost": period.get("objective_cost"),
        "total_submitted_load_mw": total_values(period.get("submitted_load_mw_by_team", {})),
        "total_generator_dispatch_mw": total_nested_values(
            period.get("generator_dispatch_mw_by_team", {})
        ),
        "total_grid_purchase_mw": total_values(period.get("grid_purchase_mw_by_team", {})),
        "total_storage_charge_mw": total_nested_values(
            period.get("storage_charge_mw_by_team", {})
        ),
        "total_storage_discharge_mw": total_nested_values(
            period.get("storage_discharge_mw_by_team", {})
        ),
        "network_branch_count": len(flow_values),
        "maximum_absolute_flow_mw": max(
            (abs(float(value)) for value in flow_values.values()),
            default=0.0,
        ),
        "constraint_bus_count": len(dual_values),
        "maximum_absolute_constraint_dual": max(
            (abs(float(value)) for value in dual_values.values()),
            default=0.0,
        ),
        "binding_diagnostic_available": bool(period.get("binding_diagnostics")),
    }


def _classroom_aggregate_projection(diagnostics: Any) -> dict[str, Any] | None:
    if diagnostics is None:
        return None
    return {
        "total_requested_load_mwh": diagnostics.total_requested_load_mwh,
        "total_projected_load_mwh": diagnostics.total_projected_load_mwh,
        "proportional_fair_utility": diagnostics.proportional_fair_utility,
        "total_load_tolerance_mwh": diagnostics.total_load_tolerance_mwh,
    }


def _classroom_team_summary(
    evaluation: BatchEvaluation,
    score: TeamScore,
    *,
    week_id: str,
    submission_number: int,
    feedback_only: bool,
    scored: bool,
    all_team_ids: set[str],
) -> dict[str, Any]:
    diagnostics = evaluation.projection_diagnostics
    projection = None
    if diagnostics is not None:
        projection = {
            "service_fraction": diagnostics.service_fraction_by_team.get(score.team_id),
            "projected_capacity_mw": diagnostics.projected_capacity_mw_by_team.get(score.team_id),
            "capped_request_capacity_mw": diagnostics.capped_request_capacity_mw_by_team.get(score.team_id),
            "requested_load_mwh": diagnostics.requested_load_mwh_by_team.get(score.team_id),
            "projected_load_mwh": diagnostics.projected_load_mwh_by_team.get(score.team_id),
        }
    score_payload = _classroom_public_score(score)
    failure_reason = score_payload.get("failure_reason")
    if isinstance(failure_reason, str) and any(
        team_id != score.team_id and team_id in failure_reason
        for team_id in all_team_ids
    ):
        score_payload["failure_reason"] = "team-specific diagnostic withheld"
    return {
        "schema_version": "classroom-private-v1",
        "team_id": score.team_id,
        "week_id": week_id,
        "submission_number": submission_number,
        "feedback_only": feedback_only,
        "scored": scored,
        "score": score_payload,
        "projection": projection,
    }


def _classroom_public_score(score: TeamScore) -> dict[str, Any]:
    payload = asdict(score)
    payload.pop("submission_key", None)
    return payload
