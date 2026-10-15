"""Instructor-facing command line entry point for CSV batch evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
from collections.abc import Mapping
from pathlib import Path
import sys
from typing import NoReturn

from .batch_evaluation import (
    evaluate_csv_batch,
    load_scoring_rules,
    write_classroom_outputs,
    write_batch_outputs,
)
from .calendars import half_year_calendar
from .round_coordinator import ClassroomRoundInput, evaluate_classroom


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser whose exits can be returned by ``main`` in tests."""

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr if status else sys.stdout)
        raise _ParserExit(status)

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _period_indices(value: str) -> tuple[int, ...]:
    pieces = [piece.strip() for piece in value.split(",")]
    if not value.strip() or any(not piece for piece in pieces):
        raise argparse.ArgumentTypeError(
            "must be a comma-separated list of non-negative integers"
        )
    try:
        indices = tuple(int(piece) for piece in pieces)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be a comma-separated list of non-negative integers"
        ) from None
    if any(index < 0 for index in indices):
        raise argparse.ArgumentTypeError("must contain only non-negative integers")
    if len(set(indices)) != len(indices):
        raise argparse.ArgumentTypeError("must not contain duplicate indices")
    return indices


def _source_period_offset(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be 0, 4344, or 4368") from None
    if parsed not in (0, 4344, 4368):
        raise argparse.ArgumentTypeError("must be 0, 4344, or 4368")
    return parsed


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load {label} {path}: {exc}") from None


def _load_region_map(path: Path) -> dict[str, str]:
    payload = _read_json(path, "region map")
    if not isinstance(payload, Mapping):
        raise ValueError("region map JSON must be an object")
    regions = payload.get("regions", payload)
    if not isinstance(regions, Mapping) or not regions:
        raise ValueError("region map must contain a non-empty object of regions")

    result: dict[str, str] = {}
    for region, value in regions.items():
        if not isinstance(region, str) or not region:
            raise ValueError("region map keys must be non-empty strings")
        if isinstance(value, Mapping):
            source_bus = value.get("source_bus_id", value.get("source_bus"))
        else:
            source_bus = value
        if not isinstance(source_bus, str) or not source_bus:
            raise ValueError(f"region map entry {region!r} must provide source_bus_id")
        result[region] = source_bus
    return result


def _evaluator_version() -> str:
    try:
        return importlib.metadata.version("ercot-game")
    except importlib.metadata.PackageNotFoundError:
        return "0.3.0"


def _summary(result) -> str:
    scored = sum(score.valid for score in result.team_scores)
    rejected = sum(not score.valid for score in result.team_scores)
    scored_label = "team" if scored == 1 else "teams"
    return (
        f"summary: {scored} {scored_label} scored, {rejected} rejected, "
        f"scope={result.competition_scope}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Run the instructor-side ERCOT CSV batch evaluator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="evaluate a directory of submissions")
    run_parser.add_argument("--submissions-dir", required=True, type=Path)
    run_parser.add_argument("--artifact-dir", required=True, type=Path)
    run_parser.add_argument("--rules", required=True, type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    run_parser.add_argument("--horizon", type=_positive_int)
    run_parser.add_argument("--year", type=int, choices=(2027, 2028, 2029), help="released instructional scenario year")
    run_parser.add_argument("--round", dest="round_name", choices=("jan_jun", "jul_dec"), help="released half-year")
    run_parser.add_argument(
        "--submission-number",
        type=int,
        choices=(1, 2),
        default=2,
        help="weekly submission number: 1 = Submission 1 (no energy-purchase cost); 2 = Submission 2 (grid purchases settled at joint-dispatch LMP; default)",
    )
    run_parser.add_argument(
        "--workers",
        required=False,
        type=_positive_int,
        default=1,
        help="parallel hourly DCOPF workers; output order remains deterministic",
    )
    run_parser.add_argument(
        "--projection-iterations",
        required=False,
        type=_positive_int,
        default=10,
        help="bisection iterations for fair continuous projection",
    )
    run_parser.add_argument(
        "--period-indices",
        required=False,
        type=_period_indices,
        help="optional comma-separated zero-based local scoring periods; defaults to the full horizon",
    )
    run_parser.add_argument(
        "--source-period-offset",
        required=False,
        type=_source_period_offset,
        default=None,
        help="source-artifact row offset: 0 for Jan-Jun or 4368 for Jul-Dec; inferred from horizon when omitted",
    )
    run_parser.add_argument(
        "--output-mode",
        choices=("full", "compact"),
        default="full",
        help="full writes per-period dispatch JSON; compact writes scores, manifests, diagnostics, and hashes only",
    )
    run_parser.add_argument(
        "--solver-backend",
        choices=("auto", "highs", "gurobi"),
        default="auto",
        help="storage recourse LP backend; auto uses licensed Gurobi when available, otherwise HiGHS",
    )
    run_parser.add_argument(
        "--region-map",
        required=False,
        type=Path,
        help="deprecated legacy region map; new submissions use artifact bus IDs directly",
    )
    classroom_parser = subparsers.add_parser(
        "classroom",
        help="evaluate the three weekly two-submission classroom schedule",
    )
    classroom_parser.add_argument("--manifest", required=True, type=Path)
    classroom_parser.add_argument("--state-path", required=False, type=Path)
    classroom_parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _manifest_path(raw_value: object, *, base_dir: Path, label: str) -> Path:
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"classroom manifest {label} must be a non-empty path")
    path = Path(raw_value)
    return path if path.is_absolute() else base_dir / path


def _classroom_round_inputs(manifest_path: Path) -> tuple[ClassroomRoundInput, ...]:
    payload = _read_json(manifest_path, "classroom manifest")
    if isinstance(payload, Mapping):
        raw_rounds = payload.get("rounds")
    else:
        raw_rounds = payload
    if not isinstance(raw_rounds, list) or len(raw_rounds) != 6:
        raise ValueError("classroom manifest must contain exactly six rounds")

    base_dir = manifest_path.parent
    inputs: list[ClassroomRoundInput] = []
    for index, raw_round in enumerate(raw_rounds, start=1):
        if not isinstance(raw_round, Mapping):
            raise ValueError(f"classroom manifest round {index} must be an object")
        round_key = raw_round.get("round_key")
        if not isinstance(round_key, str) or not round_key:
            raise ValueError(f"classroom manifest round {index} must provide round_key")
        artifact_dir = _manifest_path(
            raw_round.get("artifact_dir"),
            base_dir=base_dir,
            label=f"round {index} artifact_dir",
        )
        rules_path = _manifest_path(
            raw_round.get("rules"),
            base_dir=base_dir,
            label=f"round {index} rules",
        )
        if "horizon" in raw_round:
            horizon = _positive_int(str(raw_round["horizon"]))
        else:
            year, round_name = round_key.split("_", 1)
            horizon = half_year_calendar(int(year), round_name).horizon
        rules = load_scoring_rules(rules_path, artifact_dir=artifact_dir, horizon=horizon)
        inputs.append(
            ClassroomRoundInput(
                round_key=round_key,
                submissions=_manifest_path(
                    raw_round.get("submissions_dir"),
                    base_dir=base_dir,
                    label=f"round {index} submissions_dir",
                ),
                artifact_dir=artifact_dir,
                horizon=horizon,
                rules=rules,
            )
        )
    return tuple(inputs)


def _run_classroom(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    round_inputs = _classroom_round_inputs(manifest_path)
    output_dir = args.output_dir
    state_path = args.state_path or output_dir / "classroom_state.json"
    result = evaluate_classroom(
        state_path=state_path,
        rounds=round_inputs,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    round_inputs_by_key = {item.round_key: item for item in round_inputs}
    if len(round_inputs_by_key) != len(round_inputs):
        raise ValueError("classroom manifest contains duplicate round_key values")
    round_output_dirs: list[str] = []
    for round_result in result.submissions:
        round_input = round_inputs_by_key.get(round_result.round_key)
        if round_input is None:
            raise ValueError(
                f"classroom evaluation returned unknown round {round_result.round_key}"
            )
        dispatch = getattr(round_result.evaluation, "dispatch", None)
        periods = tuple(getattr(dispatch, "periods", ())) if dispatch is not None else ()
        period_indices = tuple(
            period.period_index for period in periods
        ) or tuple(range(round_input.horizon))
        round_output_dir = output_dir / round_result.week_id / f"submission-{round_result.submission_number}"
        write_classroom_outputs(
            round_result.evaluation,
            round_output_dir,
            week_id=round_result.week_id,
            submission_number=round_result.submission_number,
            feedback_only=round_result.feedback_only,
            scored=round_result.scored,
            scenario_version=Path(round_input.artifact_dir).name,
            evaluator_version=_evaluator_version(),
            period_indices=period_indices,
            source_period_indices=(
                getattr(round_result.evaluation, "source_period_indices", None) or None
            ),
        )
        round_output_dirs.append(round_output_dir.relative_to(output_dir).as_posix())

    public_payload = result.to_public_dict()
    public_payload["round_output_dirs"] = round_output_dirs
    serialized = json.dumps(public_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    evaluation_path = output_dir / "classroom_evaluation.json"
    evaluation_path.write_text(serialized, encoding="utf-8")
    (output_dir / "classroom_evaluation_sha256.txt").write_text(
        hashlib.sha256(serialized.encode("utf-8")).hexdigest() + "\n",
        encoding="utf-8",
    )
    print(f"output directory: {output_dir.resolve()}")
    processed_count = len(result.submissions)
    submission_label = "submission" if processed_count == 1 else "submissions"
    print(f"summary: {processed_count} half-year {submission_label}")
    return 0


def _run(args: argparse.Namespace) -> int:
    if not args.artifact_dir.is_dir():
        raise ValueError(
            f"artifact directory does not exist or is not a directory: {args.artifact_dir}"
        )
    if args.horizon is None:
        if args.year is None or args.round_name is None:
            raise ValueError("provide --horizon or both --year and --round")
        calendar = half_year_calendar(args.year, args.round_name)
        horizon = calendar.horizon
    else:
        if args.year is not None or args.round_name is not None:
            raise ValueError("--year/--round cannot be combined with --horizon")
        calendar = None
        horizon = args.horizon
    region_to_source_bus = _load_region_map(args.region_map) if args.region_map else None
    rules = load_scoring_rules(
        args.rules,
        artifact_dir=args.artifact_dir,
        horizon=horizon,
    )
    period_indices = (
        tuple(range(horizon))
        if args.period_indices is None
        else args.period_indices
    )
    # A validated ACTIVSg2000 scenario block is indexed locally from hour 0.
    # The legacy calendar offset applies only to the retired contiguous-source
    # artifacts and must not shift a half-year block into a different block.
    scenario_block_offset = (
        0
        if (args.artifact_dir / "scenario_profile.json").is_file()
        and args.source_period_offset is None
        else None
    )
    result = evaluate_csv_batch(
        args.submissions_dir,
        artifact_dir=args.artifact_dir,
        region_to_source_bus=region_to_source_bus,
        horizon=horizon,
        rules=rules,
        period_indices=period_indices,
        source_period_offset=(
            args.source_period_offset
            if args.source_period_offset is not None
            else scenario_block_offset
            if scenario_block_offset is not None
            else calendar.source_row_start
            if calendar is not None
            else None
        ),
        dispatch_workers=args.workers,
        projection_iterations=args.projection_iterations,
        storage_solver_backend=args.solver_backend,
        submission_number=args.submission_number,
    )
    write_batch_outputs(
        result,
        args.output_dir,
        scenario_version=args.artifact_dir.name,
        evaluator_version=_evaluator_version(),
        period_indices=period_indices,
        source_period_indices=result.source_period_indices,
        output_mode=args.output_mode,
    )
    print(f"output directory: {args.output_dir.resolve()}")
    print(_summary(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "classroom":
            return _run_classroom(args)
        if args.command != "run":
            parser.error("a command is required")
        return _run(args)
    except _ParserExit as exc:
        return exc.status
    except (OSError, UnicodeDecodeError, csv.Error, KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
