"""Single- and multi-agent connection-planning RL environments and adapter.

Phase 2 exposes :class:`ConnectionPlanningEnv`, Phase 4 exposes
:class:`ParallelConnectionPlanningEnv`, and Phase 1 plan-state/evaluator
delegation helpers remain available for parity and integration. Phase 6
training utilities and the solver-free Phase 7 hourly operation surface are
implemented; remaining Phase 7 gate evidence is documented in
``PHASE7_HOURLY_GATE.md``.
"""

from .adapter import (
    HalfYearHorizon,
    ParityReport,
    RLPlanState,
    compare_csv_parity,
    compare_plan_parity,
    evaluate_plan_state,
)
from .production_evaluator import ProductionEvaluatorConfig, make_production_evaluator
from .env import ConnectionPlanningEnv
from .hourly_env import HOURLY_ENVIRONMENT_VERSION, HourlyOperationEnv
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
from .parallel_replay import (
    ParallelEpisodeManifest,
    ParallelEpisodeStep,
    ParallelEpisodeTrace,
    load_parallel_replay,
    replay_parallel_episode,
    run_parallel_episode,
    save_parallel_replay,
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
from .experiments import ExperimentConfig, run_experiments
from .reporting import aggregate_records, render_json, render_markdown
from .training import LearnerCallback, TrainingConfig, train_policy

__all__ = [
    "HalfYearHorizon",
    "ParityReport",
    "RLPlanState",
    "compare_csv_parity",
    "compare_plan_parity",
    "evaluate_plan_state",
    "ProductionEvaluatorConfig",
    "make_production_evaluator",
    "ConnectionPlanningEnv",
    "HourlyOperationEnv",
    "HOURLY_ENVIRONMENT_VERSION",
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
    "ParallelEpisodeManifest",
    "ParallelEpisodeStep",
    "ParallelEpisodeTrace",
    "run_parallel_episode",
    "save_parallel_replay",
    "load_parallel_replay",
    "replay_parallel_episode",
    "benchmark_environment",
    "RELEASE_VERSION",
    "ENVIRONMENT_VERSION",
    "PLAN_SCHEMA_VERSION",
    "canonical_json",
    "stable_hash",
    "ReleaseManifest",
    "make_release_manifest",
    "ExperimentConfig",
    "run_experiments",
    "aggregate_records",
    "render_json",
    "render_markdown",
    "LearnerCallback",
    "TrainingConfig",
    "train_policy",
]
