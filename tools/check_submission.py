#!/usr/bin/env python3
"""Validate a student CSV without requiring package installation.

The checker delegates to the canonical seven-column, one-row-per-site
validator. Historical long-form fixtures are not accepted by this tool.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ercot_game.csv_validator import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
