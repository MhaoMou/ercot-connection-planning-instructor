"""Round sequencing and persistence boundary for the multi-year game.

Stage 2 deliberately stops before dispatch integration.  It provides one
small state transition API that later orchestration code can call after each
half-year submission is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .asset_state import AssetLedger
from .calendars import CLASSROOM_SCHEDULE, weekly_match_for
from .models import TeamPlan


class RoundTransitionError(ValueError):
    """Raised when a game state transition is invalid."""


def _released_round_sequence() -> tuple[str, ...]:
    return tuple(round_key for match in CLASSROOM_SCHEDULE for round_key in match.round_keys)


RELEASED_ROUND_SEQUENCE = _released_round_sequence()


@dataclass(frozen=True)
class GameState:
    """Immutable state for three two-submission weekly classroom matches."""

    state_version: int = 1
    current_round_key: str | None = None
    completed_rounds: tuple[str, ...] = ()
    asset_ledger: AssetLedger = field(default_factory=AssetLedger.empty)

    def __post_init__(self) -> None:
        completed = tuple(self.completed_rounds)
        object.__setattr__(self, "completed_rounds", completed)
        if self.state_version != 1:
            raise RoundTransitionError(
                f"unsupported game-state version: {self.state_version!r}"
            )
        if completed != RELEASED_ROUND_SEQUENCE[: len(completed)]:
            raise RoundTransitionError("completed rounds must form a contiguous released prefix")
        expected_current = completed[-1] if completed else None
        if self.current_round_key != expected_current:
            raise RoundTransitionError(
                "current_round_key must equal the last completed round"
            )

    @classmethod
    def new(cls) -> "GameState":
        return cls()

    @property
    def next_round_key(self) -> str | None:
        next_index = len(self.completed_rounds)
        if next_index >= len(RELEASED_ROUND_SEQUENCE):
            return None
        return RELEASED_ROUND_SEQUENCE[next_index]

    @property
    def current_week_id(self) -> str | None:
        """The weekly match containing the current round, if any."""
        if self.current_round_key is None:
            return None
        return weekly_match_for(self.current_round_key).week_id

    @property
    def current_submission_number(self) -> int | None:
        """The one-based submission number within the current weekly match."""
        if self.current_round_key is None:
            return None
        match = weekly_match_for(self.current_round_key)
        return match.round_keys.index(self.current_round_key) + 1

    @property
    def current_feedback_only(self) -> bool | None:
        """Whether the current submission belongs to the unranked practice week."""
        if self.current_round_key is None:
            return None
        return weekly_match_for(self.current_round_key).feedback_only

    @property
    def next_week_id(self) -> str | None:
        """The weekly match containing the next round, if any."""
        if self.next_round_key is None:
            return None
        return weekly_match_for(self.next_round_key).week_id

    @property
    def next_submission_number(self) -> int | None:
        """The one-based submission number expected next."""
        if self.next_round_key is None:
            return None
        match = weekly_match_for(self.next_round_key)
        return match.round_keys.index(self.next_round_key) + 1

    @property
    def next_feedback_only(self) -> bool | None:
        """Whether the next submission belongs to the unranked practice week."""
        if self.next_round_key is None:
            return None
        return weekly_match_for(self.next_round_key).feedback_only

    def advance(self, round_key: str, plans: Iterable[TeamPlan]) -> "GameState":
        """Commit one half-year's team plans and advance exactly one round."""
        _validate_round_key(round_key)
        expected = self.next_round_key
        if expected is None:
            raise RoundTransitionError("the released three-year game is already complete")
        if round_key != expected:
            raise RoundTransitionError(
                f"round transition expected {expected}, received {round_key}"
            )

        plan_tuple = tuple(plans)
        team_ids = [plan.team_id for plan in plan_tuple]
        if any(not team_id for team_id in team_ids):
            raise RoundTransitionError("every plan must have a non-empty team_id")
        if len(set(team_ids)) != len(team_ids):
            raise RoundTransitionError("at most one plan per team is allowed in a round")

        next_match = weekly_match_for(round_key)
        ledger = self.asset_ledger
        if self.current_week_id != next_match.week_id:
            ledger = ledger.reset()
        for plan in plan_tuple:
            ledger = ledger.register_plan(plan, round_key=round_key)
        completed = self.completed_rounds + (round_key,)
        return GameState(
            state_version=self.state_version,
            current_round_key=round_key,
            completed_rounds=completed,
            asset_ledger=ledger,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "current_round_key": self.current_round_key,
            "current_week_id": self.current_week_id,
            "current_submission_number": self.current_submission_number,
            "current_feedback_only": self.current_feedback_only,
            "completed_rounds": list(self.completed_rounds),
            "asset_ledger": self.asset_ledger.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GameState":
        if not isinstance(payload, Mapping):
            raise RoundTransitionError("game state must be an object")
        completed = payload.get("completed_rounds", ())
        if not isinstance(completed, (list, tuple)):
            raise RoundTransitionError("completed_rounds must be an array")
        ledger_payload = payload.get("asset_ledger")
        if not isinstance(ledger_payload, Mapping):
            raise RoundTransitionError("asset_ledger must be an object")
        state = cls(
            state_version=payload.get("state_version"),
            current_round_key=payload.get("current_round_key"),
            completed_rounds=tuple(completed),
            asset_ledger=AssetLedger.from_dict(ledger_payload),
        )
        serialized_metadata = {
            "current_week_id": state.current_week_id,
            "current_submission_number": state.current_submission_number,
            "current_feedback_only": state.current_feedback_only,
        }
        for field_name, expected in serialized_metadata.items():
            if field_name in payload and payload[field_name] != expected:
                raise RoundTransitionError(
                    f"{field_name} does not match current_round_key"
                )
        return state


def _validate_round_key(round_key: str) -> None:
    if not isinstance(round_key, str) or round_key not in RELEASED_ROUND_SEQUENCE:
        raise RoundTransitionError(
            "round_key must identify a released half-year in 2027, 2028, or 2029"
        )
