"""Summarize completed instructor-side cohort evaluations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = (
    "cohort_size", "repetition", "rank", "team_id", "valid", "feasible",
    "score", "served_load_mwh", "it_value", "energy_cost",
    "site_cost", "generator_capital_cost", "generator_operating_cost",
    "storage_capital_cost", "failure_reason",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    for path in sorted(args.results_root.glob("groups_*_rep_*/evaluation.json")):
        parts = path.parent.name.split("_")
        cohort_size = int(parts[1])
        repetition = int(parts[3])
        payload = json.loads(path.read_text(encoding="utf-8"))
        scores = payload.get("team_scores", [])
        for score in scores:
            row = {
                "cohort_size": cohort_size,
                "repetition": repetition,
                **{field: score.get(field) for field in FIELDS if field not in {"cohort_size", "repetition"}},
            }
            rows.append(row)
    rows.sort(key=lambda row: (int(row["cohort_size"]), int(row["repetition"]), int(row["rank"])))
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary: list[dict[str, object]] = []
    for size in sorted({int(row["cohort_size"]) for row in rows}):
        subset = [row for row in rows if int(row["cohort_size"]) == size]
        scores = [float(row["score"]) for row in subset if row["valid"]]
        by_rep = []
        for rep in sorted({int(row["repetition"]) for row in subset}):
            rep_rows = [row for row in subset if int(row["repetition"]) == rep]
            by_rep.append({"repetition": rep, "scores": rep_rows})
        summary.append({
            "cohort_size": size,
            "team_count": len(subset),
            "valid_count": sum(bool(row["valid"]) for row in subset),
            "score_min": min(scores),
            "score_max": max(scores),
            "score_mean": sum(scores) / len(scores),
            "repetitions": by_rep,
        })
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summarized {len(rows)} team scores across {len(summary)} cohort sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
