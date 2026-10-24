"""Fail-closed mapping contract for Backtest Studio -> RetailBot2 promotion.

Backtest strategy IDs and RetailBot2 strategy IDs belong to different registries.
A candidate must carry an explicit, reviewed compatibility mapping before it can
be translated into an executable RetailBot2 strategy/mode. This helper validates
that mapping only; it does not activate paper trading or enable Telegram.
"""
from typing import Any, Dict, Iterable, Optional


def resolve_retailbot2_promotion_target(
    candidate: Dict[str, Any],
    executable_identities: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Return a validated RetailBot2 target, or None when mapping is unsafe.

    Expected candidate metadata:
      strategy: source Backtest Studio strategy identity
      retailbot2_mapping: {
        strategy_id: executable RetailBot2 identity,
        mode: "shadow" | "inverse",
        compatible: True,
        mapping_version: non-empty reviewed mapping version
      }

    No implicit name matching or fallback is permitted. The executable identity
    list must come from the current RetailBot2 registry, not a promotion allowlist.
    """
    if not isinstance(candidate, dict):
        return None
    source_id = candidate.get("strategy")
    if not isinstance(source_id, str) or not source_id.strip():
        return None

    mapping = candidate.get("retailbot2_mapping")
    if not isinstance(mapping, dict) or mapping.get("compatible") is not True:
        return None

    target_id = mapping.get("strategy_id")
    mode = mapping.get("mode")
    version = mapping.get("mapping_version")
    if not isinstance(target_id, str) or not target_id.strip():
        return None
    if mode not in {"shadow", "inverse"}:
        return None
    if not isinstance(version, str) or not version.strip():
        return None

    identities = list(executable_identities)
    match = next(
        (
            row for row in identities
            if isinstance(row, dict)
            and row.get("strategy_id") == target_id
            and row.get("mode") == mode
        ),
        None,
    )
    if match is None or match.get("historical_only") is True:
        return None

    return {
        "source_strategy": source_id.strip(),
        "strategy_id": target_id.strip(),
        "mode": mode,
        "mapping_version": version.strip(),
    }
