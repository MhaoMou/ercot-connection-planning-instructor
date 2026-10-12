"""Single- and multi-agent connection-planning RL environments and adapter.

Phase 2 exposes :class:`ConnectionPlanningEnv`, Phase 4 exposes
:class:`ParallelConnectionPlanningEnv`, and Phase 1 plan-state/evaluator
delegation helpers remain available for parity and integration. Hourly
operation and research-training utilities remain deferred.
"""

from .adapter import (
    HalfYearHorizon,
    ParityReport,
    RLPlanState,
    compare_csv_parity,
    compare_plan_parity,
    evaluate_plan_state,
)
from .env import ConnectionPlanningEnv
from .parallel_env import ParallelConnectionPlanningEnv
from .baselines import GreedyPolicy, Policy, RandomPolicy, RuleBasedPolicy
from .replay import (
    EpisodeManifest,
    EpisodeStep,
    EpisodeTrace,
    load_replay,
    replay_episode,
    run_episode,
    save_replay,
)
from .benchmarks import benchmark_environment
from .release import (
    ENVIRONMENT_VERSION,
    PLAN_SCHEMA_VERSION,
    RELEASE_VERSION,
    ReleaseManifest,
    canonical_json,
    make_release_manifest,
    stable_hash,
)

__all__ = [
    "HalfYearHorizon",
    "ParityReport",
    "RLPlanState",
    "compare_csv_parity",
    "compare_plan_parity",
    "evaluate_plan_state",
    "ConnectionPlanningEnv",
    "ParallelConnectionPlanningEnv",
    "Policy",
    "RandomPolicy",
    "GreedyPolicy",
    "RuleBasedPolicy",
    "EpisodeManifest",
    "EpisodeStep",
    "EpisodeTrace",
    "run_episode",
    "save_replay",
    "load_replay",
    "replay_episode",
    "benchmark_environment",
    "RELEASE_VERSION",
    "ENVIRONMENT_VERSION",
    "PLAN_SCHEMA_VERSION",
    "canonical_json",
    "stable_hash",
    "ReleaseManifest",
    "make_release_manifest",
]
