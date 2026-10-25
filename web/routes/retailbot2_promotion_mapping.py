'''Fail-closed mapping contract for Backtest Studio -> RetailBot2 promotion.

Backtest strategy IDs and RetailBot2 strategy IDs belong to different registries.
Only a server-sourced, reviewed mapping registry may authorize translation to an
executable RetailBot2 identity. Candidate payload metadata is untrusted and must
never self-attest compatibility. This helper validates mapping only; it does not
activate paper trading or enable Telegram.
'''
from typing import Any, Dict, Iterable, Optional


def resolve_retailbot2_promotion_target(
    candidate: Dict[str, Any],
    executable_identities: Iterable[Dict[str, Any]],
    mapping_registry: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    '''Return a validated RetailBot2 target, or None when mapping is unsafe.

    mapping_registry is server-owned configuration keyed by exact source
    Backtest Studio strategy identity. Each record must explicitly provide
    strategy_id, mode (shadow or inverse), compatible=True, and a non-empty
    reviewed mapping_version. Never use mapping metadata supplied by the candidate.

    The executable identity list must come from the current RetailBot2 registry,
    not a promotion allowlist. Missing or malformed registries fail closed.
    '''
    if not isinstance(candidate, dict):
        return None
    source_id = candidate.get("strategy")
    if not isinstance(source_id, str) or not source_id.strip():
        return None
    source_id = source_id.strip()

    if not isinstance(mapping_registry, dict):
        return None
    mapping = mapping_registry.get(source_id)
    if not isinstance(mapping, dict) or mapping.get("compatible") is not True:
        return None

    target_id = mapping.get("strategy_id")
    mode = mapping.get("mode")
    version = mapping.get("mapping_version")
    if not isinstance(target_id, str) or not target_id.strip():
        return None
    if not isinstance(mode, str) or mode not in {"shadow", "inverse"}:
        return None
    if not isinstance(version, str) or not version.strip():
        return None

    try:
        identities = list(executable_identities)
    except TypeError:
        return None
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
        "source_strategy": source_id,
        "strategy_id": target_id.strip(),
        "mode": mode,
        "mapping_version": version.strip(),
    }
