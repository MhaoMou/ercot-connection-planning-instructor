from pathlib import Path

from ercot_game.release_config import (
    CANONICAL_STORAGE_BOUNDARY_MODE,
    CANONICAL_TIME_STEP_HOURS,
    canonical_artifact_dir,
    canonical_rules_path,
)


def test_canonical_release_uses_six_hour_daily_cycle() -> None:
    assert CANONICAL_TIME_STEP_HOURS == 6
    assert CANONICAL_STORAGE_BOUNDARY_MODE == "daily_exact_cycle"


def test_canonical_paths_are_block_specific(tmp_path: Path) -> None:
    assert canonical_artifact_dir(2028, "jan_jun", root=tmp_path) == (
        tmp_path / "data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun"
    )
    assert canonical_rules_path("jul_dec", root=tmp_path) == (
        tmp_path / "data/teaching/dispatch_scoring_rules_jul_dec_6h_v1.json"
    )
