"""Deterministic instructor-side CSV collection and batch evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import csv
import hashlib
import json
from copy import copy
from pathlib import Path
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import math
from typing import Any, TYPE_CHECKING, NoReturn

from .csv_submission import (
    CSVSubmission,
    TEAM_GENERATOR_COST_CURVE,
    load_bus_ids,
    read_student_csv,
    read_submission_identity,
)
from .models import TeamPlan
from .models import ConnectionPlan
from .economic_profiles import (
    HourlyEconomicProfiles,
    load_economic_profiles,
    load_economic_profiles_artifact,
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
    site_cost_per_site: float = 0.0
    infeasible_score: float = 0.0
    default_generator_cost_per_mwh: float = 100.0
    storage_capital_cost_per_mw: float = 0.0
    storage_capital_cost_per_mwh: float = 0.0
    storage_operating_cost_per_mwh: float = 0.0
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
        _validate_rule_number("infeasible_score", self.infeasible_score)
        _validate_rule_number(
            "default_generator_cost_per_mwh",
            self.default_generator_cost_per_mwh,
            nonnegative=True,
        )
        for name in ("storage_capital_cost_per_mw", "storage_capital_cost_per_mwh", "storage_operating_cost_per_mwh"):
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
    storage_operating_cost: float = 0.0


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
    if artifact_dir is not None and horizon in (4368, 4416):
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
        site_cost_per_site=float(payload.get("site_cost_per_site", 0.0)),
        infeasible_score=float(payload.get("infeasible_score", 0.0)),
        default_generator_cost_per_mwh=float(payload.get("default_generator_cost_per_mwh", 100.0)),
        storage_capital_cost_per_mw=float(payload.get("storage_capital_cost_per_mw", 0.0)),
        storage_capital_cost_per_mwh=float(payload.get("storage_capital_cost_per_mwh", 0.0)),
        storage_operating_cost_per_mwh=float(payload.get("storage_operating_cost_per_mwh", 0.0)),
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
) -> TeamScore:
    period_indices = tuple(period.period_index for period in dispatch.periods)
    if len(set(period_indices)) != len(period_indices):
        raise ValueError("dispatch contains duplicate period index entries")
    periods_by_index = {period.period_index: period for period in dispatch.periods}
    team_id = plan.team_id
    generator_by_id = {generator.generator_id: generator for generator in plan.generators}
    served = it_value = energy_cost = generator_cost = storage_operating_cost = 0.0
    for period_index, period in periods_by_index.items():
        load = period.submitted_load_mw_by_team[team_id]
        generation = sum(period.generator_dispatch_mw_by_team[team_id].values())
        profiles = rules.economic_profiles
        value = profiles.it_value_for(period_index) if profiles is not None else rules.it_value_per_mwh[period_index]
        served += load * rules.time_step_hours
        it_value += load * value * rules.time_step_hours
        grid_purchase_by_team = getattr(period, "grid_purchase_mw_by_team", None)
        if profiles is not None:
            purchases_by_bus = getattr(period, "grid_purchase_mw_by_team_by_bus", None)
            if purchases_by_bus is None or team_id not in purchases_by_bus:
                raise ValueError("dispatch must provide bus-level grid purchases for hourly economic profiles")
            energy_cost += sum(
                amount * profiles.price_for_bus(bus_id, period_index)
                for bus_id, amount in purchases_by_bus[team_id].items()
            ) * rules.time_step_hours
        else:
            price = rules.energy_price_per_mwh[period_index]
            if grid_purchase_by_team is not None:
                grid_purchase = grid_purchase_by_team.get(team_id, max(0.0, load - generation))
            else:
                grid_purchase = max(0.0, load - generation)
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
        storage_discharge_by_team = getattr(period, "storage_discharge_mw_by_team", None)
        if storage_discharge_by_team is not None:
            storage_operating_cost += sum(storage_discharge_by_team.get(team_id, {}).values()) * rules.storage_operating_cost_per_mwh * rules.time_step_hours
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
    site_count = (
        len(plan.connections)
        if site_cost_connection_count_by_team is None
        else site_cost_connection_count_by_team.get(team_id, len(plan.connections))
    )
    site_cost = site_count * rules.site_cost_per_site
    score = it_value - energy_cost - generator_cost - generator_capital - storage_capital - storage_operating_cost - site_cost
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
        storage_operating_cost=storage_operating_cost,
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
        storage_operating_cost=0.0,
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
            )
        )
    return tuple(remapped)


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
    """Scale each team's requested load continuously for fair projection."""
    connections = tuple(
        ConnectionPlan(
            connection.connection_id,
            connection.bus_id,
            connection.capacity_mw * alpha,
            connection.profile,
        )
        for connection in plan.connections
    )
    return TeamPlan(
        plan.team_id,
        connections=connections,
        generators=plan.generators,
        storages=plan.storages,
    )


_FAIR_PROJECTION_MIN_SERVICE_FRACTION = 1e-6
_FAIR_PROJECTION_TOLERANCE = 1e-6


def _fair_continuous_projection(
    artifact_dir: str | Path,
    plans: tuple[TeamPlan, ...],
    period_indices: tuple[int, ...],
    source_period_indices: tuple[int, ...],
    *,
    projection_iterations: int = 10,
) -> tuple[BatchDispatchResult, float, tuple[TeamPlan, ...]] | None:
    """Find the largest common team service fraction using continuous bisection.

    The zero-service endpoint is evaluated explicitly and is retained as a
    fair result when no positive service fraction is feasible.  The caller's
    iteration count is treated as a lower bound; the evaluator always runs
    enough iterations to resolve ``_FAIR_PROJECTION_TOLERANCE``.  This makes
    the minimum positive service fraction explicit instead of inheriting the
    old implicit ``1 / 2**projection_iterations`` cutoff.
    """
    if projection_iterations <= 0:
        raise ValueError("projection_iterations must be positive")
    if not (Path(artifact_dir) / "bus.csv").is_file():
        return None

    zero_plans = tuple(_scaled_plan(plan, 0.0) for plan in plans)
    try:
        zero_dispatch = _dispatch_plans(
            artifact_dir,
            zero_plans,
            period_indices,
            source_period_indices=source_period_indices,
            stop_on_infeasible=True,
        )
    except (DCOPFInfeasible, DCOPFUnresolved, ValueError, NotImplementedError):
        return None

    low, high = 0.0, 1.0
    best: tuple[BatchDispatchResult, float, tuple[TeamPlan, ...]] | None = (
        zero_dispatch,
        0.0,
        zero_plans,
    )
    minimum_iterations = math.ceil(math.log2(1.0 / _FAIR_PROJECTION_TOLERANCE))
    iterations = max(projection_iterations, minimum_iterations)
    for _ in range(iterations):
        alpha = (low + high) / 2.0
        candidate_plans = tuple(_scaled_plan(plan, alpha) for plan in plans)
        try:
            candidate_dispatch = _dispatch_plans(
                artifact_dir,
                candidate_plans,
                period_indices,
                source_period_indices=source_period_indices,
                stop_on_infeasible=True,
            )
        except (DCOPFInfeasible, DCOPFUnresolved, ValueError, NotImplementedError):
            high = alpha
        else:
            if alpha >= _FAIR_PROJECTION_MIN_SERVICE_FRACTION:
                low = alpha
                best = (candidate_dispatch, alpha, candidate_plans)
            else:
                low = alpha
        if high - low <= _FAIR_PROJECTION_TOLERANCE:
            break
    return best


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
) -> BatchEvaluation:
    """Collect and score valid submissions with deterministic failure isolation.

    ``plan_overrides`` changes only the evaluator-facing plans used for
    dispatch and operational scoring. CSV parsing, accepted/rejected records,
    and input hashes remain based on the submitted files. ``capital_cost_scope``
    optionally limits one-time generator/storage construction charges; when it
    is omitted, all assets in each evaluator-facing plan are charged. The
    optional site-cost scope lets a multi-round coordinator charge only the
    current submission's connection sites; direct callers default to all plan
    connections.
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
    plans = _evaluation_plans(accepted, plan_overrides)
    _validate_capital_cost_scope(capital_cost_scope, plans)
    _validate_site_cost_scope(site_cost_connection_count_by_team, plans)
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
            )
            if projection is not None:
                projected_dispatch, service_fraction, projected_plans = projection
                dispatch = projected_dispatch
                batch_scope = "joint_projection"
                dispatch_records.append(
                    DispatchRecord(
                        scope="joint_projection",
                        attempted_team_ids=attempted_ids,
                        attempted_submission_keys=attempted_submission_keys,
                        status="feasible",
                        dispatch=projected_dispatch,
                    )
                )
                isolation_events.append(
                    FailureIsolationEvent(
                        attempted_team_ids=attempted_ids,
                        removed_team_id=None,
                        outcome="joint_projected",
                        reason=(
                            "continuous common service fraction="
                            f"{service_fraction:.12g}; "
                            "minimum positive service fraction="
                            f"{_FAIR_PROJECTION_MIN_SERVICE_FRACTION:.12g}; "
                            "bisection tolerance="
                            f"{_FAIR_PROJECTION_TOLERANCE:.12g}"
                        ),
                    )
                )
                scores.extend(
                    replace(
                        _score_team(
                            original_plan,
                            projected_dispatch,
                            rules,
                            submission_key=record_by_team[original_plan.team_id].submission_key,
                            capital_cost_scope=capital_cost_scope,
                            site_cost_connection_count_by_team=site_cost_connection_count_by_team,
                        ),
                        competition_scope="joint_projection",
                    )
                    for original_plan in plans
                )
                # Skip discrete team-removal isolation: all valid teams receive
                # the same continuously projected service fraction.
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
            "site_cost_per_site": rules.site_cost_per_site,
            "infeasible_score": rules.infeasible_score,
            "default_generator_cost_per_mwh": generator_cost,
            "storage_capital_cost_per_mw": rules.storage_capital_cost_per_mw,
            "storage_capital_cost_per_mwh": rules.storage_capital_cost_per_mwh,
            "storage_operating_cost_per_mwh": rules.storage_operating_cost_per_mwh,
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
    "storage_operating_cost",
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
    return (
        json.dumps(
            payload,
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
