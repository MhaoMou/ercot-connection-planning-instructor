"""Generate a deterministic eight-team instructor demonstration round."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = (
    "team_id",
    "site_id",
    "bus_id",
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
)

# These are public bus IDs from the released 2028 January--June block. The
# repeated 7049 request deliberately demonstrates joint competition for one
# electrically attractive location without exposing real student strategy.
PLANS: dict[str, tuple[tuple[str, str, int, int, int], ...]] = {
    "team-01": (("site-01-a", "7049", 80, 24, 12), ("site-01-b", "4071", 50, 0, 0)),
    "team-02": (("site-02-a", "7049", 70, 25, 12), ("site-02-b", "5449", 45, 15, 0)),
    "team-03": (("site-03-a", "7229", 60, 18, 8), ("site-03-b", "5385", 40, 0, 0)),
    "team-04": (("site-04-a", "6199", 55, 20, 10), ("site-04-b", "5103", 35, 0, 0)),
    "team-05": (("site-05-a", "7039", 50, 15, 8), ("site-05-b", "4040", 30, 0, 0)),
    "team-06": (("site-06-a", "5084", 45, 12, 6), ("site-06-b", "7004", 25, 0, 0)),
    "team-07": (("site-07-a", "7248", 40, 12, 6), ("site-07-b", "5465", 25, 0, 0)),
    "team-08": (("site-08-a", "7201", 35, 10, 5), ("site-08-b", "7183", 20, 0, 0)),
}


def generate(*, artifact_dir: Path, output_dir: Path) -> None:
    with (artifact_dir / "bus.csv").open(newline="", encoding="utf-8") as handle:
        bus_ids = {row["bus_id"] for row in csv.DictReader(handle)}
    if not bus_ids:
        raise ValueError(f"no bus IDs found in {artifact_dir / 'bus.csv'}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for team_id, sites in PLANS.items():
        path = output_dir / f"{team_id}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for site_id, bus_id, connection, generator, storage in sites:
                if bus_id not in bus_ids:
                    raise ValueError(f"{team_id}/{site_id}: unknown scenario bus {bus_id}")
                if not 0 <= generator <= connection:
                    raise ValueError(f"{team_id}/{site_id}: invalid generator capacity")
                writer.writerow(
                    {
                        "team_id": team_id,
                        "site_id": site_id,
                        "bus_id": bus_id,
                        "connection_capacity_mw": connection,
                        "generator_capacity_mw": generator,
                        "storage_power_mw": storage,
                        "storage_energy_mwh": 4 * storage,
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("data/processed/activsg2000_scenarios_v1/2028/jan_jun"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/instructor_manual/examples/eight_team_round/submissions"),
    )
    args = parser.parse_args()
    generate(artifact_dir=args.artifact_dir, output_dir=args.output_dir)
    print(f"generated {len(PLANS)} submissions in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
