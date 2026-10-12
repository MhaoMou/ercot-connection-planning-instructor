"""Phase 2 single-agent sequential connection-planning environment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from pathlib import Path
from typing import Any
import uuid

import numpy as np

from ercot_game.models import SitePlan
from ercot_game.calendars import half_year_calendar

from .adapter import RLPlanState, evaluate_plan_state
from .release import ReleaseManifest

try:  # Gymnasium is optional so the core tests can run in a minimal install.
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - exercised by environments without Gymnasium
    gym = None

    class _Discrete:
        def __init__(self, n):
            if int(n) <= 0:
                raise ValueError("n must be positive")
            self.n = int(n)
            self.shape = ()
            self.dtype = np.dtype(np.int64)
            self._rng = np.random.default_rng()

        def seed(self, seed=None):
            self._rng = np.random.default_rng(seed)
            return [seed]

        def sample(self):
            return np.int64(self._rng.integers(self.n))

        def contains(self, value):
            array = np.asarray(value)
            return array.shape == () and np.issubdtype(array.dtype, np.integer) and 0 <= int(array) < self.n

    class _Box:
        def __init__(self, low, high, shape=None, dtype=np.float32):
            self.dtype = np.dtype(dtype)
            self.low = np.broadcast_to(np.asarray(low, dtype=self.dtype), shape).copy() if shape is not None else np.asarray(low, dtype=self.dtype)
            self.high = np.broadcast_to(np.asarray(high, dtype=self.dtype), shape).copy() if shape is not None else np.asarray(high, dtype=self.dtype)
            self.shape = self.low.shape
            self._rng = np.random.default_rng()

        def seed(self, seed=None):
            self._rng = np.random.default_rng(seed)
            return [seed]

        def sample(self):
            return np.asarray(self._rng.uniform(self.low, self.high), dtype=self.dtype)

        def contains(self, value):
            value = np.asarray(value)
            return value.shape == self.shape and np.all(np.isfinite(value)) and np.all(value >= self.low) and np.all(value <= self.high)

    class _MultiBinary:
        def __init__(self, n):
            self.n = n
            self.shape = (n,)
            self._rng = np.random.default_rng()

        def seed(self, seed=None):
            self._rng = np.random.default_rng(seed)
            return [seed]

        def sample(self):
            return self._rng.integers(0, 2, size=self.shape, dtype=np.int8)

        def contains(self, value):
            value = np.asarray(value)
            return value.shape == self.shape and np.all((value == 0) | (value == 1))

    class _Dict:
        def __init__(self, spaces_map):
            self.spaces = dict(spaces_map)

        def seed(self, seed=None):
            seeds = []
            for offset, space in enumerate(self.spaces.values()):
                seeds.extend(space.seed(None if seed is None else seed + offset))
            return seeds

        def sample(self):
            return {key: space.sample() for key, space in self.spaces.items()}

        def contains(self, value):
            return isinstance(value, Mapping) and set(value) == set(self.spaces) and all(
                self.spaces[key].contains(value[key]) for key in self.spaces
            )

    class _FallbackSpaces:
        Discrete = _Discrete
        Box = _Box
        MultiBinary = _MultiBinary
        Dict = _Dict

    spaces = _FallbackSpaces()


_ENVIRONMENT_VERSION = "phase2-single-agent-v1"
_ACTION_SIZE = 7
_ACTION_FIELDS = (
    "bus_index",
    "requested_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
    "finish",
    "revise",
)


class _StructuredActionSpace(spaces.Dict):
    """Dict action space with statically valid samples for the plan schema."""

    def sample(self, mask=None):
        del mask
        sampled = {key: space.sample() for key, space in self.spaces.items()}
        sampled["generator_capacity_mw"] = np.asarray(0.0, dtype=np.float32)
        sampled["storage_power_mw"] = np.asarray(0.0, dtype=np.float32)
        sampled["storage_energy_mwh"] = np.asarray(0.0, dtype=np.float32)
        sampled["finish"] = np.int64(0)
        sampled["revise"] = np.int64(0)
        return sampled


class ConnectionPlanningEnv(gym.Env if gym is not None else object):
    """A compact sequential planner for one team's site-level connection plan.

    The declared action space is a structured mapping with discrete
    ``bus_index``, ``finish``, and ``revise`` fields plus bounded scalar
    capacity fields. For compatibility, the legacy vector
    ``[bus_index, requested_mw, generator_mw, storage_power_mw,
    storage_energy_mwh, finish, revise]`` is also accepted by :meth:`step`.
    A normal action adds one bus; ``revise=1`` updates the existing site at
    that bus.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        team_id: str,
        bus_catalog: Sequence[str],
        scenario_year: int,
        scenario_block: str,
        max_sites: int = 16,
        max_planning_steps: int = 16,
        max_episode_steps: int | None = None,
        max_capacity_mw: float = 10000.0,
        invalid_action_policy: str = "penalize",
        invalid_action_penalty: float = -1.0,
        reward_mode: str = "terminal_score",
        evaluator_callback: Callable[..., Any] | None = None,
        evaluator_artifact_dir: str | Path | None = None,
        evaluator_rules: Any | None = None,
        submission_dir: str | Path | None = None,
        scenario_manifest_hash: str | None = None,
        rules_version: str | None = None,
        rules_hash: str | None = None,
        evaluator_version: str | None = None,
        release_manifest: ReleaseManifest | None = None,
    ) -> None:
        if not isinstance(team_id, str) or not team_id:
            raise ValueError("team_id must be a non-empty string")
        catalog = tuple(bus_catalog)
        if not catalog or any(not isinstance(bus, str) or not bus for bus in catalog):
            raise ValueError("bus_catalog must contain non-empty bus IDs")
        if len(set(catalog)) != len(catalog):
            raise ValueError("bus_catalog must not contain duplicates")
        if not isinstance(scenario_year, int) or isinstance(scenario_year, bool):
            raise ValueError("scenario_year must be a non-bool integer")
        if scenario_block not in {"jan_jun", "jul_dec"}:
            raise ValueError("scenario_block must be 'jan_jun' or 'jul_dec'")
        horizon = half_year_calendar(scenario_year, scenario_block)
        if max_sites <= 0 or max_planning_steps <= 0:
            raise ValueError("max_sites and max_planning_steps must be positive")
        if max_episode_steps is not None and max_episode_steps <= 0:
            raise ValueError("max_episode_steps must be positive when configured")
        if not math.isfinite(max_capacity_mw) or max_capacity_mw <= 0:
            raise ValueError("max_capacity_mw must be finite and positive")
        if invalid_action_policy not in {"penalize", "raise"}:
            raise ValueError("invalid_action_policy must be 'penalize' or 'raise'")
        if reward_mode not in {"terminal_score", "dense_surrogate"}:
            raise ValueError("reward_mode must be 'terminal_score' or 'dense_surrogate'")
        if not math.isfinite(invalid_action_penalty):
            raise ValueError("invalid_action_penalty must be finite")

        self.team_id = team_id
        self.bus_catalog = catalog
        self.scenario_year = scenario_year
        self.scenario_block = scenario_block
        self.horizon = horizon
        self.max_sites = int(max_sites)
        self.max_planning_steps = int(max_planning_steps)
        self.max_episode_steps = max_episode_steps
        self.max_capacity_mw = float(max_capacity_mw)
        self.invalid_action_policy = invalid_action_policy
        self.invalid_action_penalty = float(invalid_action_penalty)
        self.reward_mode = reward_mode
        self.evaluator_callback = evaluator_callback
        self.evaluator_artifact_dir = evaluator_artifact_dir
        self.evaluator_rules = evaluator_rules
        self.submission_dir = Path(submission_dir or ".")
        self.scenario_manifest_hash = scenario_manifest_hash
        self.rules_version = rules_version
        self.rules_hash = rules_hash
        self.evaluator_version = evaluator_version
        if release_manifest is not None and not isinstance(release_manifest, ReleaseManifest):
            raise TypeError("release_manifest must be a ReleaseManifest or None")
        if release_manifest is not None and (
            release_manifest.scenario_year != scenario_year
            or release_manifest.scenario_block != scenario_block
        ):
            raise ValueError("release_manifest scenario does not match environment scenario")
        self.release_manifest = (
            None if release_manifest is None else ReleaseManifest.from_dict(release_manifest.to_dict())
        )
        self.action_space = _StructuredActionSpace({
            "bus_index": spaces.Discrete(len(catalog)),
            "requested_capacity_mw": spaces.Box(0.0, max_capacity_mw, shape=(), dtype=np.float32),
            "generator_capacity_mw": spaces.Box(0.0, max_capacity_mw, shape=(), dtype=np.float32),
            "storage_power_mw": spaces.Box(0.0, max_capacity_mw, shape=(), dtype=np.float32),
            "storage_energy_mwh": spaces.Box(0.0, 4 * max_capacity_mw, shape=(), dtype=np.float32),
            "finish": spaces.Discrete(2),
            "revise": spaces.Discrete(2),
        })
        self.observation_space = spaces.Dict({
            "metadata": spaces.Box(0, np.inf, shape=(6,), dtype=np.float32),
            "bus_catalog": spaces.Box(0, np.inf, shape=(len(catalog), 2), dtype=np.float32),
            "own_plan": spaces.Box(0, np.inf, shape=(self.max_sites, 5), dtype=np.float32),
            "own_plan_mask": spaces.MultiBinary(self.max_sites),
        })
        self._rng = np.random.default_rng()
        self._sites: list[SitePlan] = []
        self._step_count = 0
        self._episode_steps = 0
        self._finished = False
        self._seed: int | None = None

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        if gym is not None:
            super().reset(seed=seed)
        elif seed is not None:
            self._rng = np.random.default_rng(seed)
        self._seed = seed
        self._sites = []
        self._step_count = 0
        self._episode_steps = 0
        self._finished = False
        return self._observation(), self._base_info()

    def step(self, action):
        if self._finished:
            raise RuntimeError("episode is complete; call reset() before step()")
        self._episode_steps += 1
        values = self._coerce_action(action)
        diagnostic = self._validate_action(values)
        if diagnostic is not None:
            if self.invalid_action_policy == "raise":
                raise ValueError(diagnostic[1])
            reward = self.invalid_action_penalty
            self._step_count += 1
            terminated, truncated, reason = self._limits()
            info = self._base_info(valid_action=False)
            info.update({"invalid_action_code": diagnostic[0], "diagnostic": diagnostic[1]})
            if diagnostic[0] == "empty_plan":
                self._finished = True
                info.update({
                    "terminated_reason": "empty_plan",
                    "dispatch_status": "not_evaluated",
                })
                return self._observation(), reward, True, False, info
            if terminated and self._sites:
                terminal_reward, terminal_info = self._finish()
                del terminal_reward
                info = info | terminal_info
                info.update({
                    "valid_action": False,
                    "invalid_action_code": diagnostic[0],
                    "diagnostic": diagnostic[1],
                    "terminated_reason": f"{reason}_after_invalid_action",
                })
            elif terminated:
                info["terminated_reason"] = f"{reason}_after_invalid_action"
            elif truncated:
                info["truncated_reason"] = reason
            if terminated or truncated:
                self._finished = True
            return self._observation(), reward, terminated, truncated, info

        self._step_count += 1
        if values[5] >= 0.5:
            self._finished = True
            reward, info = self._finish()
            if self.reward_mode == "dense_surrogate":
                reward = 0.0
            return self._observation(), reward, True, False, info | {"terminated_reason": "finish"}

        bus_index = int(round(values[0]))
        site = SitePlan(
            site_id=f"site-{bus_index + 1:04d}",
            bus_id=self.bus_catalog[bus_index],
            connection_capacity_mw=float(values[1]),
            generator_capacity_mw=float(values[2]),
            storage_power_mw=float(values[3]),
            storage_energy_mwh=float(values[4]),
        )
        existing = next((i for i, current in enumerate(self._sites) if current.bus_id == site.bus_id), None)
        if existing is None:
            self._sites.append(site)
        else:
            self._sites[existing] = site
        reward = self._dense_reward(site) if self.reward_mode == "dense_surrogate" else 0.0
        terminated, truncated, reason = self._limits()
        if terminated or truncated:
            self._finished = True
        info = self._base_info(valid_action=True)
        if terminated:
            terminal_reward, terminal_info = self._finish()
            if self.reward_mode == "terminal_score":
                reward = terminal_reward
            info = info | terminal_info
            info["valid_action"] = True
            info["terminated_reason"] = reason
        if terminated:
            info["terminated_reason"] = reason
        if truncated:
            info["truncated_reason"] = reason
        return self._observation(), reward, terminated, truncated, info

    def _validate_action(self, values: np.ndarray):
        if values.shape != (_ACTION_SIZE,):
            return "invalid_shape", "action must have shape (7,)"
        if not np.all(np.isfinite(values)):
            return "nonfinite_action", "action values must be finite"
        if values[0] < 0 or values[0] >= len(self.bus_catalog) or not math.isclose(values[0], round(values[0]), abs_tol=1e-6):
            return "unknown_bus", "bus index is outside the public bus catalog"
        if np.any(values[1:5] < 0):
            return "negative_capacity", "capacities must be non-negative"
        if values[2] > values[1]:
            return "generator_exceeds_connection", "generator capacity cannot exceed connection capacity"
        if np.any(values[1:4] > self.max_capacity_mw) or values[4] > 4 * self.max_capacity_mw:
            return "capacity_out_of_bounds", "capacity exceeds the configured action bound"
        if values[5] not in (0, 1) or values[6] not in (0, 1):
            return "invalid_flag", "finish and revise must be 0 or 1"
        if values[5] == 1:
            if not self._sites:
                return "empty_plan", "finish requires at least one site"
            return None
        if values[3] == 0 and values[4] != 0:
            return "storage_without_power", "storage energy requires positive storage power"
        if values[3] > 0 and not math.isclose(values[4], 4 * values[3], rel_tol=1e-6, abs_tol=1e-6):
            return "storage_duration_mismatch", "storage energy must equal four times storage power"
        bus_id = self.bus_catalog[int(round(values[0]))]
        if not values[6] and any(site.bus_id == bus_id for site in self._sites):
            return "duplicate_bus_without_revise", "revising an existing bus requires revise=1"
        if values[6] and not any(site.bus_id == bus_id for site in self._sites):
            return "revise_unknown_site", "revise=1 requires an existing site at that bus"
        if not values[6] and len(self._sites) >= self.max_sites:
            return "site_limit", "maximum site count reached"
        return None

    @staticmethod
    def _coerce_action(action) -> np.ndarray:
        if isinstance(action, Mapping):
            if set(action) != set(_ACTION_FIELDS):
                return np.asarray([], dtype=np.float64)
            try:
                return np.asarray([action[field] for field in _ACTION_FIELDS], dtype=np.float64)
            except (TypeError, ValueError):
                return np.asarray([], dtype=np.float64)
        try:
            return np.asarray(action, dtype=np.float64)
        except (TypeError, ValueError):
            return np.asarray([], dtype=np.float64)

    def _limits(self):
        if self._step_count >= self.max_planning_steps:
            return True, False, "max_planning_steps"
        if self.max_episode_steps is not None and self._episode_steps >= self.max_episode_steps:
            return False, True, "max_episode_steps"
        return False, False, None

    def _finish(self):
        if self.evaluator_callback is None:
            return 0.0, self._base_info(score=0.0, dispatch_status="not_evaluated")
        self.submission_dir.mkdir(parents=True, exist_ok=True)
        state = RLPlanState.for_half_year(
            team_id=self.team_id,
            sites=self._sites,
            year=self.scenario_year,
            round_name=self.scenario_block,
        )
        result = evaluate_plan_state(
            state,
            submission_path=self.submission_dir / f"submission-{uuid.uuid4().hex}.csv",
            artifact_dir=self.evaluator_artifact_dir or self.submission_dir,
            rules=self.evaluator_rules,
            evaluator=self.evaluator_callback,
        )
        score = _extract_score(result, self.team_id)
        return score, self._base_info(score=score, dispatch_status="evaluated")

    def _dense_reward(self, site: SitePlan) -> float:
        return site.connection_capacity_mw + 0.1 * site.generator_capacity_mw + 0.05 * site.storage_power_mw

    def _observation(self):
        plan = np.zeros((self.max_sites, 5), dtype=np.float32)
        mask = np.zeros(self.max_sites, dtype=np.int8)
        for index, site in enumerate(self._sites[: self.max_sites]):
            plan[index] = (self.bus_catalog.index(site.bus_id), site.connection_capacity_mw, site.generator_capacity_mw, site.storage_power_mw, site.storage_energy_mwh)
            mask[index] = 1
        block_number = 1 if self.scenario_block == "jan_jun" else 2
        return {
            "metadata": np.asarray([1, self.scenario_year, block_number, self._step_count, len(self._sites), int(self._finished)], dtype=np.float32),
            "bus_catalog": np.asarray([[index, 1] for index in range(len(self.bus_catalog))], dtype=np.float32),
            "own_plan": plan,
            "own_plan_mask": mask,
        }

    def current_plan_state(self) -> RLPlanState | None:
        """Return this team's current immutable plan state, if non-empty.

        The returned state contains only this environment's sites.  Empty plans
        are represented by ``None`` because the canonical evaluator contract
        requires at least one site.
        """
        if not self._sites:
            return None
        return RLPlanState.for_half_year(
            team_id=self.team_id,
            sites=tuple(self._sites),
            year=self.scenario_year,
            round_name=self.scenario_block,
        )

    def _base_info(self, *, valid_action: bool | None = None, score: float | None = None, dispatch_status: str = "not_run"):
        info = {
            "environment_version": _ENVIRONMENT_VERSION,
            "scenario_year": self.scenario_year,
            "scenario_block": self.scenario_block,
            "max_capacity_mw": self.max_capacity_mw,
            "max_sites": self.max_sites,
            "max_planning_steps": self.max_planning_steps,
            "seed": self._seed,
            "step": self._step_count,
            "valid_action": valid_action,
            "plan": [_site_dict(site) for site in self._sites],
            "dispatch_status": dispatch_status,
            "scenario_manifest_hash": self.scenario_manifest_hash,
            "rules_version": self.rules_version,
            "rules_hash": self.rules_hash,
            "evaluator_version": self.evaluator_version,
            "release_version": None if self.release_manifest is None else self.release_manifest.release_version,
            "release_environment_version": (
                None if self.release_manifest is None else self.release_manifest.environment_version
            ),
            "release_plan_schema_version": (
                None if self.release_manifest is None else self.release_manifest.plan_schema_version
            ),
            "release_manifest_digest": None if self.release_manifest is None else self.release_manifest.digest(),
        }
        if score is not None:
            info["score"] = score
        return info


def _site_dict(site: SitePlan) -> dict[str, Any]:
    return {
        "site_id": site.site_id,
        "bus_id": site.bus_id,
        "requested_capacity_mw": site.connection_capacity_mw,
        "generator_capacity_mw": site.generator_capacity_mw,
        "storage_power_mw": site.storage_power_mw,
        "storage_energy_mwh": site.storage_energy_mwh,
    }


def _extract_score(result: Any, team_id: str) -> float:
    scores = getattr(result, "team_scores", result)
    if isinstance(scores, Mapping):
        record = scores[team_id]
    else:
        record = next(item for item in scores if getattr(item, "team_id", None) == team_id)
    score = getattr(record, "score", record["score"] if isinstance(record, Mapping) else None)
    if score is None or not math.isfinite(float(score)):
        raise ValueError("evaluator result must contain a finite team score")
    return float(score)
