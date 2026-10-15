"""Source-checkout shim for the canonical package under ``src``."""

from pathlib import Path


__path__ = [str(Path(__file__).resolve().parent.parent / "src" / "ercot_game")]
