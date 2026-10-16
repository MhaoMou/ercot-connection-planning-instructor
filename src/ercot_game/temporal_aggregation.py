"""Utilities for energy-preserving fixed-width time aggregation.

The classroom release uses six-hour operating intervals to represent a
half-year with roughly one month of optimization periods.  The functions in
this module keep the distinction explicit: capacities and rates remain in MW
or USD/MWh, while interval energy and flow-based economics are accounted for
with the returned ``time_step_hours`` value.
"""

from __future__ import annotations

from collections.abc import Sequence
import math


DEFAULT_TIME_STEP_HOURS = 6


def _validate_time_step(time_step_hours: int) -> int:
    if (
        not isinstance(time_step_hours, int)
        or isinstance(time_step_hours, bool)
        or time_step_hours <= 0
    ):
        raise ValueError("time_step_hours must be a positive integer")
    return time_step_hours


def aggregated_horizon(source_horizon: int, time_step_hours: int = DEFAULT_TIME_STEP_HOURS) -> int:
    """Return the number of aggregate periods in a source horizon."""

    _validate_time_step(time_step_hours)
    if not isinstance(source_horizon, int) or isinstance(source_horizon, bool) or source_horizon <= 0:
        raise ValueError("source_horizon must be a positive integer")
    if source_horizon % time_step_hours:
        raise ValueError("source_horizon must be divisible by time_step_hours")
    return source_horizon // time_step_hours


def source_period_indices(source_horizon: int, time_step_hours: int = DEFAULT_TIME_STEP_HOURS) -> tuple[int, ...]:
    """Return the first source-hour index represented by each aggregate period."""

    horizon = aggregated_horizon(source_horizon, time_step_hours)
    return tuple(period * time_step_hours for period in range(horizon))


def aggregate_hourly_values(
    values: Sequence[float],
    time_step_hours: int = DEFAULT_TIME_STEP_HOURS,
) -> tuple[float, ...]:
    """Average consecutive source-hour values into fixed-width intervals.

    Averaging MW rates, loads, and availability fractions preserves the total
    energy represented by the source series once each aggregate value is
    multiplied by ``time_step_hours``.  The caller must keep the returned
    values in the same physical units as the source values.
    """

    _validate_time_step(time_step_hours)
    try:
        normalized = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        raise ValueError("values must contain finite numeric values") from None
    if not normalized:
        raise ValueError("values must not be empty")
    if any(not math.isfinite(value) for value in normalized):
        raise ValueError("values must contain finite numeric values")
    aggregated_horizon(len(normalized), time_step_hours)
    return tuple(
        sum(normalized[offset : offset + time_step_hours]) / time_step_hours
        for offset in range(0, len(normalized), time_step_hours)
    )
