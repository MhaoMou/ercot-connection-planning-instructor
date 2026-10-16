from __future__ import annotations

import json
from pathlib import Path

import pytest

from ercot_game.calendars import half_year_calendar
from ercot_game.scenario_profiles import demand_multiplier, hourly_state
from ercot_game.temporal_aggregation import (
    aggregate_hourly_values,
    aggregated_horizon,
    source_period_indices,
)


def test_six_hour_mean_aggregation_preserves_block_average() -> None:
    assert aggregate_hourly_values((0.0, 6.0, 12.0, 18.0, 24.0, 30.0), 6) == pytest.approx((15.0,))


def test_aggregation_requires_complete_blocks() -> None:
    with pytest.raises(ValueError, match="divisible"):
        aggregate_hourly_values((1.0, 2.0, 3.0, 4.0, 5.0), 6)


@pytest.mark.parametrize(
    ("year", "round_name", "expected"),
    ((2028, "jan_jun", 728), (2028, "jul_dec", 736), (2027, "jan_jun", 724)),
)
def test_six_hour_half_year_horizons_and_source_indices(year: int, round_name: str, expected: int) -> None:
    source_horizon = half_year_calendar(year, round_name).horizon
    assert aggregated_horizon(source_horizon, 6) == expected
    indices = source_period_indices(source_horizon, 6)
    assert len(indices) == expected
    assert indices[:3] == (0, 6, 12)
    assert indices[-1] == source_horizon - 6


def test_scenario_profile_averages_six_hour_load_blocks(tmp_path: Path) -> None:
    (tmp_path / "bus_load_base.csv").write_text("bus_id,base_load_mw\nB1,10\n", encoding="utf-8")
    (tmp_path / "generator_catalog.csv").write_text(
        "generator_id,bus_id,p_min_mw,p_max_mw,marginal_cost_c1_per_mwh,status,profile_type\n"
        "g1,B1,0,100,10,1,thermal\n",
        encoding="utf-8",
    )
    (tmp_path / "profile_parameters.json").write_text(
        json.dumps(
            {
                "generators": [
                    {"generator_id": "g1", "profile_type": "thermal", "seed": 1}
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenario_profile.json").write_text(
        json.dumps(
            {
                "year": 2028,
                "calendar_block": "jan_jun",
                "time_step_hours": 6,
                "aggregation_method": "mean",
            }
        ),
        encoding="utf-8",
    )

    demand, availability = hourly_state(
        tmp_path,
        year=2028,
        calendar_block="jan_jun",
        period_index=0,
    )

    expected = sum(10.0 * demand_multiplier(hour, year=2028) for hour in range(6)) / 6.0
    assert demand["B1"] == pytest.approx(round(expected, 8))
    assert availability["g1"] == pytest.approx(100.0)
    with pytest.raises(IndexError, match="outside the half-year block"):
        hourly_state(tmp_path, year=2028, calendar_block="jan_jun", period_index=728)
