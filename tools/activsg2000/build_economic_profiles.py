"""Build deterministic hourly economic-profile inputs for teaching blocks."""

from __future__ import annotations

import json
import hashlib
import io
import math
from pathlib import Path
from typing import Any
import zipfile

import numpy as np

from .dcopf import (
    extract_lmp_by_bus,
    solve_prepared_period,
    validate_balance_dual_sign,
)


IT_VALUE_PROFILE_VERSION = "it-service-value-v3-ai-intensive"
DEFAULT_IT_VALUE_PER_MWH = 1500.0
LMP_PROFILE_VERSION = "activsg2000-baseline-dcopf-lmp-v1"


def build_it_value_profile(
    horizon_hours: int,
    *,
    base_value_per_mwh: float = DEFAULT_IT_VALUE_PER_MWH,
) -> list[float]:
    """Return one deterministic IT-service value for every operating hour.

    The approved AI-intensive profile is represented explicitly at the full
    scoring horizon. It is not a repeated six-value diurnal vector. The value
    is the opportunity value of served computational load, not an
    electricity-market price.
    """

    if not isinstance(horizon_hours, int) or isinstance(horizon_hours, bool):
        raise ValueError("horizon_hours must be an integer")
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if not math.isfinite(base_value_per_mwh) or base_value_per_mwh <= 0:
        raise ValueError("base_value_per_mwh must be finite and positive")
    return [float(base_value_per_mwh)] * horizon_hours


def it_value_metadata(horizon_hours: int, *, base_value_per_mwh: float = DEFAULT_IT_VALUE_PER_MWH) -> dict[str, Any]:
    """Return provenance metadata for a generated IT-value vector."""

    build_it_value_profile(horizon_hours, base_value_per_mwh=base_value_per_mwh)
    return {
        "profile_version": IT_VALUE_PROFILE_VERSION,
        "horizon_hours": horizon_hours,
        "base_value_per_mwh": float(base_value_per_mwh),
        "profile_name": "AI-intensive",
        "method": "explicit constant full-horizon instructor profile",
        "representation": "explicit full-horizon profile",
        "units": "USD/MWh of served computational load",
        "interpretation": "opportunity value of served IT load, not an electricity-market price",
        "source_category": "INSTRUCTOR-CHOSEN",
    }


def write_it_value_profile(path: str | Path, horizon_hours: int, *, base_value_per_mwh: float = DEFAULT_IT_VALUE_PER_MWH) -> None:
    """Write a standalone JSON profile artifact for a scenario block."""

    payload = {
        "it_value_per_mwh": build_it_value_profile(
            horizon_hours, base_value_per_mwh=base_value_per_mwh
        ),
        "it_value_provenance": it_value_metadata(
            horizon_hours, base_value_per_mwh=base_value_per_mwh
        ),
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bus_ids(artifact_dir: Path) -> list[str]:
    import csv

    with (artifact_dir / "bus.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    bus_ids = [str(row["bus_id"]) for row in rows]
    if not bus_ids or len(set(bus_ids)) != len(bus_ids):
        raise ValueError("bus.csv must contain a non-empty unique bus_id sequence")
    return bus_ids


def _write_deterministic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """Write an unchanging compressed NumPy archive for reproducible hashes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(arrays):
            buffer = io.BytesIO()
            np.lib.format.write_array(buffer, np.asarray(arrays[name]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, buffer.getvalue())


def build_baseline_lmp_profile(
    artifact_dir: str | Path,
    output_dir: str | Path,
    *,
    # A 1e-4 MW perturbation is below the useful numerical resolution of the
    # large ACTIVSg2000 LP and can make a correct dual appear inconsistent.
    # 1e-2 MW remains negligible for the dispatch while producing a stable
    # finite-difference sign check.
    epsilon_mw: float = 1e-2,
    require_validated: bool = True,
) -> dict[str, Any]:
    """Generate a deterministic hourly baseline LMP artifact for one block.

    The student plan is absent by construction.  The resulting matrices are
    indexed ``[hour, bus]`` and contain both the raw balance dual and the
    non-negative scoring LMP.  A one-hour finite-difference perturbation is
    solved before generation to validate the dual sign convention.
    """

    artifact_root = Path(artifact_dir)
    output_root = Path(output_dir)
    manifest_path = artifact_root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"scenario block manifest is missing: {manifest_path}")
    block_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if require_validated and block_manifest.get("validation_status") != "passed":
        raise ValueError("scenario block must be DCOPF-validated before LMP generation")
    horizon = block_manifest.get("horizon")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        raise ValueError("scenario block manifest must contain a positive integer horizon")
    if not math.isfinite(epsilon_mw) or epsilon_mw <= 0:
        raise ValueError("epsilon_mw must be finite and positive")
    bus_ids = _read_bus_ids(artifact_root)

    base_result = solve_prepared_period(artifact_root, period_index=0)
    perturb_bus = next((bus_id for bus_id in bus_ids if bus_id in base_result.balance_dual_by_bus), None)
    if perturb_bus is None:
        raise ValueError("baseline DCOPF result has no balance duals for the artifact buses")
    perturbed_result = solve_prepared_period(
        artifact_root,
        period_index=0,
        additional_demand_mw_by_bus={perturb_bus: epsilon_mw},
    )
    dual_sign_validation = validate_balance_dual_sign(
        base_result,
        perturbed_result,
        bus_id=perturb_bus,
        epsilon=epsilon_mw,
    )
    if dual_sign_validation["sign"] != "positive":
        raise ValueError("unexpected negative balance-dual sign; refusing to generate LMPs")

    raw_duals = np.empty((horizon, len(bus_ids)), dtype=np.float64)
    scoring_lmps = np.empty_like(raw_duals)
    for period_index in range(horizon):
        result = base_result if period_index == 0 else solve_prepared_period(artifact_root, period_index=period_index)
        raw_by_bus = result.balance_dual_by_bus
        if set(raw_by_bus) != set(bus_ids):
            raise ValueError(f"period {period_index} balance dual bus set does not match bus.csv")
        raw_duals[period_index, :] = [float(raw_by_bus[bus_id]) for bus_id in bus_ids]
        scoring_lmps[period_index, :] = [extract_lmp_by_bus(result, clip_negative=True)[bus_id] for bus_id in bus_ids]
    if not np.isfinite(raw_duals).all() or not np.isfinite(scoring_lmps).all():
        raise ValueError("baseline LMP artifact contains non-finite values")
    if (scoring_lmps < 0).any():
        raise ValueError("baseline scoring LMP artifact contains negative values")

    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / "economic_profiles.npz"
    _write_deterministic_npz(
        artifact_path,
        {"raw_balance_dual": raw_duals, "lmp_per_mwh": scoring_lmps},
    )
    it_profile_path = output_root / "economic_profile.json"
    it_profile_payload = {
        "it_value_per_mwh": build_it_value_profile(horizon),
        "it_value_provenance": it_value_metadata(horizon),
    }
    it_profile_path.write_text(
        json.dumps(it_profile_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metadata: dict[str, Any] = {
        "profile_version": LMP_PROFILE_VERSION,
        "horizon_hours": horizon,
        "bus_ids": bus_ids,
        "price_source": "baseline ACTIVSg2000 DCOPF balance duals",
        "price_interpretation": "instructional model-derived nodal marginal energy prices; not official ERCOT market prices",
        "raw_dual_units": "objective currency per MWh",
        "scoring_lmp_units": "USD/MWh",
        "normalization": "raw balance duals clipped at zero for scoring; raw values retained in artifact",
        "dual_sign_validation": dual_sign_validation,
        "solver": "HiGHS through tools.activsg2000.dcopf.solve_prepared_period",
        "student_plan_included": False,
        "artifact_sha256": _sha256_file(artifact_path),
        "economic_profile_json_sha256": _sha256_file(it_profile_path),
        "artifact_files": {
            "lmp_npz": artifact_path.name,
            "economic_profile_json": it_profile_path.name,
            "manifest": "economic_profile_manifest.json",
        },
        "it_value_provenance": it_profile_payload["it_value_provenance"],
    }
    (output_root / "economic_profile_manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate baseline hourly ACTIVSg2000 DCOPF LMPs")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-unvalidated", action="store_true")
    args = parser.parse_args()
    metadata = build_baseline_lmp_profile(
        args.artifact_dir,
        args.output_dir,
        require_validated=not args.allow_unvalidated,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
