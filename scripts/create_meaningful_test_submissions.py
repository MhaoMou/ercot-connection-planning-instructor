"""Create legacy complete-horizon CSV fixtures for compatibility tests.

The generated ten-column hourly files are historical fixtures, not current
student submissions. Current submissions use the canonical one-row-per-site
seven-column schema.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = (
    "team_id",
    "submission_id",
    "period",
    "bus_id",
    "load_mw",
    "generator_id",
    "generator_capacity_mw",
    "storage_id",
    "storage_power_mw",
    "storage_energy_mwh",
)


def write_team(
    output_dir: Path,
    *,
    team_id: str,
    bus_id: str,
    horizon: int,
    load_by_period: list[float],
    storage: tuple[str, float, float] | None = None,
) -> None:
    path = output_dir / f"{team_id}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for period, load in enumerate(load_by_period, start=1):
            storage_id, power, energy = storage or ("", "", "")
            writer.writerow(
                {
                    "team_id": team_id,
                    "submission_id": "meaningful-half-year-test",
                    "period": period,
                    "bus_id": bus_id,
                    "load_mw": f"{load:.6f}",
                    "generator_id": "",
                    "generator_capacity_mw": "",
                    "storage_id": storage_id,
                    "storage_power_mw": power,
                    "storage_energy_mwh": energy,
                }
            )


def create_submissions(output_dir: Path, horizon: int, oversized_load: float | None = None) -> None:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_team(output_dir, team_id="team-alpha", bus_id="3001001", horizon=horizon, load_by_period=[20.0] * horizon)
    write_team(
        output_dir,
        team_id="team-bravo",
        bus_id="3001002",
        horizon=horizon,
        load_by_period=[15.0 if period % 24 < 12 else 18.0 for period in range(horizon)],
    )
    write_team(
        output_dir,
        team_id="team-charlie-storage",
        bus_id="3001003",
        horizon=horizon,
        load_by_period=[18.0 if period % 24 < 12 else 22.0 for period in range(horizon)],
        storage=("storage-charlie", 5.0, 10.0),
    )
    delta_load = oversized_load if oversized_load is not None else 10.0
    write_team(output_dir, team_id="team-delta", bus_id="3001004", horizon=horizon, load_by_period=[delta_load] * horizon)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--horizon", type=int, default=4368)
    parser.add_argument(
        "--oversized-load",
        type=float,
        help="replace the delta team's load with a deliberately infeasible stress load",
    )
    args = parser.parse_args()
    create_submissions(args.output_dir, args.horizon, args.oversized_load)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
