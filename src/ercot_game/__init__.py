"""Public canonical model for the ACTIVSg2000 dispatch evaluator."""

from importlib import import_module


_LAZY_EXPORTS = {
    "ConnectionPlan": (".models", "ConnectionPlan"),
    "GeneratorPlan": (".models", "GeneratorPlan"),
    "SitePlan": (".models", "SitePlan"),
    "StoragePlan": (".models", "StoragePlan"),
    "TeamPlan": (".models", "TeamPlan"),
    "AssetLedger": (".asset_state", "AssetLedger"),
    "AssetStateError": (".asset_state", "AssetStateError"),
    "GeneratorAsset": (".asset_state", "GeneratorAsset"),
    "StorageAsset": (".asset_state", "StorageAsset"),
    "TeamAssetState": (".asset_state", "TeamAssetState"),
    "GameState": (".game_state", "GameState"),
    "RELEASED_ROUND_SEQUENCE": (".game_state", "RELEASED_ROUND_SEQUENCE"),
    "RoundTransitionError": (".game_state", "RoundTransitionError"),
    "GameStateStore": (".game_state_store", "GameStateStore"),
    "GameStateStoreError": (".game_state_store", "GameStateStoreError"),
    "RoundCoordinatorError": (".round_coordinator", "RoundCoordinatorError"),
    "RoundEvaluationResult": (".round_coordinator", "RoundEvaluationResult"),
    "ClassroomRoundInput": (".round_coordinator", "ClassroomRoundInput"),
    "ClassroomEvaluationResult": (".round_coordinator", "ClassroomEvaluationResult"),
    "WeeklyRanking": (".round_coordinator", "WeeklyRanking"),
    "evaluate_classroom": (".round_coordinator", "evaluate_classroom"),
    "evaluate_round": (".round_coordinator", "evaluate_round"),
    "CapitalCostScope": (".batch_evaluation", "CapitalCostScope"),
}


def __getattr__(name: str):
    """Load public evaluator objects only when a caller requests them."""
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value

__version__ = "0.3.0"

__all__ = [
    "ConnectionPlan",
    "GeneratorPlan",
    "SitePlan",
    "StoragePlan",
    "TeamPlan",
    "AssetLedger",
    "AssetStateError",
    "GeneratorAsset",
    "StorageAsset",
    "TeamAssetState",
    "GameState",
    "RELEASED_ROUND_SEQUENCE",
    "RoundTransitionError",
    "GameStateStore",
    "GameStateStoreError",
    "RoundCoordinatorError",
    "RoundEvaluationResult",
    "ClassroomRoundInput",
    "ClassroomEvaluationResult",
    "WeeklyRanking",
    "evaluate_classroom",
    "evaluate_round",
    "CapitalCostScope",
]
