"""Released calendar metadata for multi-year half-year submissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import calendar as _calendar


@dataclass(frozen=True)
class HalfYearCalendar:
    """One released half-year of one-hour local scoring periods."""

    year: int
    round_name: str
    start_date: date
    end_date: date
    horizon: int
    source_row_start: int

    @property
    def label(self) -> str:
        return f"{self.year}_{self.round_name}"


SUPPORTED_YEARS = (2027, 2028, 2029)
_ROUND_MONTHS = {"jan_jun": (1, 6), "jul_dec": (7, 12)}


def half_year_calendar(year: int, round_name: str) -> HalfYearCalendar:
    """Return the exact hourly calendar for a released half-year."""
    if year not in SUPPORTED_YEARS:
        raise ValueError(f"year must be one of the supported years: {SUPPORTED_YEARS}")
    if round_name not in _ROUND_MONTHS:
        raise ValueError("round_name must be jan_jun or jul_dec")
    first_month, last_month = _ROUND_MONTHS[round_name]
    start = date(year, first_month, 1)
    end = date(year, last_month, _calendar.monthrange(year, last_month)[1])
    horizon = (end - start).days * 24 + 24
    return HalfYearCalendar(
        year=year,
        round_name=round_name,
        start_date=start,
        end_date=end,
        horizon=horizon,
        source_row_start=0 if round_name == "jan_jun" else (
            (date(year, 7, 1) - date(year, 1, 1)).days * 24
        ),
    )


def hourly_timestamps(year: int) -> tuple[str, ...]:
    """Return UTC-style hour-start labels for a calendar year."""
    start = datetime(year, 1, 1)
    hours = 366 if _calendar.isleap(year) else 365
    return tuple(
        (start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")
        for index in range(hours * 24)
    )
