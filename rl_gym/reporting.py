"""Privacy-safe aggregation and reporting for public RL experiment records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any


_LATENCY_FIELDS = (
    ("reset", "reset_latency_seconds"),
    ("step", "step_latency_seconds"),
    ("episode", "episode_latency_seconds"),
    ("evaluation", "eval_latency_seconds"),
)
_SENSITIVE_PARTS = (
    "evaluator",
    "solver",
    "dispatch",
    "projection",
    "private",
    "plan",
    "callback",
    "raw_response",
    "raw-response",
    "rawresponse",
)
_ALLOWED_SANITIZED_KEYS = {
    "projection_applied",
    "projected_team_count",
    "evaluated",
    "joint_evaluated",
    "team_count",
    "finished_team_count",
    "valid_team_count",
    "terminated_team_count",
    "truncated_team_count",
    "unknown_action_count",
    "evaluation_error",
    "counts",
    "statuses",
    "timing",
    "manifest_digest",
    "release_manifest_digest",
}
_ALLOWED_TOP_LEVEL = {
    "policy", "scenario_year", "scenario_block", "team_count", "reward",
    "status", "terminated", "truncated", "release_manifest_digest",
    "timing", "aggregate", "episode_index", "episode_seed", "team_id",
}


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _reject_sensitive_keys(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            normalized_key = key_text.replace("-", "_").replace(" ", "_")
            if (
                key_text not in _ALLOWED_SANITIZED_KEYS
                and any(part in key_text or part in normalized_key for part in _SENSITIVE_PARTS)
            ):
                raise ValueError(f"private field is not allowed: {path}.{key}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def _validate_record(record: Any, index: int) -> dict[str, Any]:
    record = _mapping(record, f"record {index}")
    _reject_sensitive_keys(record, f"record {index}")
    unknown = set(record) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ValueError(f"record {index} has unsupported field(s): {', '.join(sorted(unknown))}")
    required = _ALLOWED_TOP_LEVEL - {"timing", "aggregate", "episode_index", "episode_seed", "team_id"}
    missing = required - set(record)
    if missing:
        raise ValueError(f"record {index} missing required field(s): {', '.join(sorted(missing))}")

    policy = record["policy"]
    if not isinstance(policy, str) or not policy:
        raise ValueError(f"record {index} policy must be a non-empty string")
    year = record["scenario_year"]
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError(f"record {index} scenario_year must be an integer")
    block = record["scenario_block"]
    if not isinstance(block, str) or not block:
        raise ValueError(f"record {index} scenario_block must be a non-empty string")
    team_count = _positive_int(record["team_count"], f"record {index} team_count")
    if "episode_index" in record:
        if isinstance(record["episode_index"], bool) or not isinstance(record["episode_index"], int) or record["episode_index"] < 0:
            raise ValueError(f"record {index} episode_index must be a non-negative integer")
    if "episode_seed" in record:
        if isinstance(record["episode_seed"], bool) or not isinstance(record["episode_seed"], int):
            raise ValueError(f"record {index} episode_seed must be an integer")
    if "team_id" in record and (not isinstance(record["team_id"], str) or not record["team_id"]):
        raise ValueError(f"record {index} team_id must be a non-empty string")
    reward = _finite_number(record["reward"], f"record {index} reward")
    status = record["status"]
    if not isinstance(status, str) or not status:
        raise ValueError(f"record {index} status must be a non-empty string")
    for field in ("terminated", "truncated"):
        if not isinstance(record[field], bool):
            raise ValueError(f"record {index} {field} must be a bool")
    digest = record["release_manifest_digest"]
    if not isinstance(digest, str) or not digest:
        raise ValueError(f"record {index} release_manifest_digest must be a non-empty string")
    timing = _mapping(record.get("timing", {}), f"record {index} timing")
    aggregate = _mapping(record.get("aggregate", {}), f"record {index} aggregate")
    for field, value in timing.items():
        if field in {name for _, name in _LATENCY_FIELDS} or field in {"steps", "policy_calls", "action_count"}:
            _finite_number(value, f"record {index} timing.{field}")
    if "unknown_action_count" in aggregate:
        _finite_number(aggregate["unknown_action_count"], f"record {index} aggregate.unknown_action_count")
    return {
        "policy": policy,
        "scenario_year": year,
        "scenario_block": block,
        "team_count": team_count,
        "reward": reward,
        "status": status,
        "terminated": record["terminated"],
        "truncated": record["truncated"],
        "release_manifest_digest": digest,
        "timing": dict(timing),
        "aggregate": dict(aggregate),
    }


def _summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {"mean": mean, "std": math.sqrt(variance)}


def _group_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewards = [record["reward"] for record in records]
    action_counts = {"policy_calls": 0, "action_count": 0, "steps": 0, "unknown_actions": 0}
    for record in records:
        timing = record["timing"]
        action_counts["policy_calls"] += int(timing.get("policy_calls", 0))
        action_counts["action_count"] += int(timing.get("action_count", 0))
        action_counts["steps"] += int(timing.get("steps", 0))
        action_counts["unknown_actions"] += int(record["aggregate"].get("unknown_action_count", 0))
    latency = {}
    for label, field in _LATENCY_FIELDS:
        values = [float(record["timing"][field]) for record in records if field in record["timing"]]
        if values:
            summary = _summary(values)
            summary["min"] = min(values)
            summary["max"] = max(values)
            latency[label] = summary

    status_groups: dict[str, list[dict[str, Any]]] = {"not_evaluated": [], "evaluator_backed": []}
    status_values = set()
    for record in records:
        status_values.add(record["status"])
        bucket = "not_evaluated" if record["status"] == "not_evaluated" else "evaluator_backed"
        status_groups[bucket].append(record)
    status = {}
    for bucket, bucket_records in status_groups.items():
        status[bucket] = {
            "records": len(bucket_records),
            "reward": _summary([record["reward"] for record in bucket_records]),
        }
    status_summaries = {
        status_value: {
            "records": sum(record["status"] == status_value for record in records),
            "reward": _summary([record["reward"] for record in records if record["status"] == status_value]),
        }
        for status_value in sorted(status_values)
    }
    return {
        "key": {
            "policy": records[0]["policy"],
            "scenario_year": records[0]["scenario_year"],
            "scenario_block": records[0]["scenario_block"],
            "team_count": records[0]["team_count"],
        },
        "records": len(records),
        "reward": _summary(rewards),
        "termination_rates": {
            "terminated": sum(record["terminated"] for record in records) / len(records),
            "truncated": sum(record["truncated"] for record in records) / len(records),
        },
        "action_counts": action_counts,
        "latency_seconds": latency,
        "manifest_digests": sorted({record["release_manifest_digest"] for record in records}),
        "status": status,
        "status_summaries": status_summaries,
        "status_values": sorted(status_values),
    }


def aggregate_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate and aggregate public records returned by ``run_experiments``."""
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or not records:
        raise ValueError("records must be a non-empty sequence")
    validated = [_validate_record(record, index) for index, record in enumerate(records)]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in validated:
        key = (record["policy"], record["scenario_year"], record["scenario_block"], record["team_count"])
        grouped.setdefault(key, []).append(record)
    groups = [_group_report(grouped[key]) for key in sorted(grouped)]
    return {
        "groups": groups,
        "policies": sorted({record["policy"] for record in validated}),
        "records": len(validated),
    }


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# RL Gym Baseline Report",
        "",
        "| Policy | Scenario | Teams | Records | Reward mean ± std | Policy calls | Actions | Steps | Unknown actions | Terminated | Truncated | Statuses | Latency | Manifest digests |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for group in report["groups"]:
        key = group["key"]
        reward = group["reward"]
        action_counts = group["action_counts"]
        latency = "; ".join(
            f"{label} mean/std/min/max={values['mean']:.6g}/{values['std']:.6g}/"
            f"{values['min']:.6g}/{values['max']:.6g}s"
            for label, values in group["latency_seconds"].items()
        ) or "none"
        statuses = "; ".join(
            f"{status}: {summary['records']} records, reward "
            f"{summary['reward']['mean']:.6g} ± {summary['reward']['std']:.6g}"
            for status, summary in group["status_summaries"].items()
        )
        lines.append(
            f"| {key['policy']} | {key['scenario_year']}/{key['scenario_block']} | "
            f"{key['team_count']} | {group['records']} | {reward['mean']:.6g} ± {reward['std']:.6g} | "
            f"{action_counts['policy_calls']} | {action_counts['action_count']} | {action_counts['steps']} | "
            f"{action_counts['unknown_actions']} | "
            f"{group['termination_rates']['terminated']:.3f} | {group['termination_rates']['truncated']:.3f} | "
            f"{statuses} | {latency} | {', '.join(group['manifest_digests'])} |"
        )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None, help="JSON array path; stdin when omitted")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None, *, stdin_text: str | None = None) -> int:
    args = _parser().parse_args(argv)
    raw = args.input.read_text(encoding="utf-8") if args.input is not None else (
        sys.stdin.read() if stdin_text is None else stdin_text
    )
    payload = json.loads(raw)
    report = aggregate_records(payload)
    output = render_json(report) if args.format == "json" else render_markdown(report)
    sys.stdout.write(output)
    return 0


__all__ = ["aggregate_records", "render_json", "render_markdown", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
