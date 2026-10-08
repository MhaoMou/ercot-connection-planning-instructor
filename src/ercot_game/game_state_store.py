"""Durable storage for the versioned multi-year :class:`GameState`."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .asset_state import AssetStateError
from .game_state import GameState, RoundTransitionError


class GameStateStoreError(RuntimeError):
    """Raised when a persisted game-state file cannot be used."""


class GameStateStore:
    """Read and atomically write one deterministic JSON game-state file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, state: GameState) -> None:
        """Write ``state`` using a sibling temporary file and ``os.replace``."""
        if not isinstance(state, GameState):
            raise GameStateStoreError("state must be a GameState instance")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        ) + "\n"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError as exc:
            raise GameStateStoreError(
                f"could not save game state to {self.path}: {exc}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def load(self) -> GameState:
        """Load and validate the persisted state."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except (OSError, UnicodeError) as exc:
            raise GameStateStoreError(
                f"could not read game state from {self.path}: {exc}"
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GameStateStoreError(
                f"malformed JSON in game state {self.path}: {exc.msg}"
            ) from exc
        try:
            return GameState.from_dict(payload)
        except (AssetStateError, RoundTransitionError, TypeError, ValueError) as exc:
            raise GameStateStoreError(
                f"invalid game state in {self.path}: {exc}"
            ) from exc
