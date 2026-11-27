"""Saved rule definitions shared by backtests and independent paper workers."""
import json
import logging
from pathlib import Path

from backtest.simulate import simulate

CUSTOM_FILE = str(Path(__file__).resolve().parents[1] / "custom_strategies.json")


def read_definitions(path):
    try:
        with open(path, encoding="utf-8") as stream:
            definitions = json.load(stream)
    except FileNotFoundError:
        return []
    if not isinstance(definitions, list) or any(not isinstance(d, dict) for d in definitions):
        raise ValueError("Invalid custom strategy definition file")
    return definitions


def paper_strategy_registry(registry, path=None):
    # A dashboard-triggered tick may already have web-registered custom runners.
    # Replace those with the same database snapshot an independent worker sees.
    current = {name: fn for name, fn in registry.items() if getattr(fn, "__module__", None) != __name__}
    try:
        from db.custom_strategy_store import list_definitions
        definitions = read_definitions(path) if path is not None else list_definitions()
    except Exception as exc:
        logging.getLogger(__name__).warning("Cannot read saved custom strategies (%s)", type(exc).__name__)
        return current
    for definition in definitions:
        if not definition.get("enabled", True):
            continue
        try:
            name = definition["name"]
            if name in current or f"inverse_{name}" in current:
                continue
            current[name], current[f"inverse_{name}"] = _create_strategy_runner(definition)
        except (KeyError, TypeError, ValueError):
            logging.getLogger(__name__).exception("Invalid saved strategy %r", definition.get("name"))
    return current


INDICATOR_ALIASES = {
    "relative_strength_index": "rsi",
    "rsi_14": "rsi",
    "volume": "volume_ratio",
    "vol": "volume_ratio",
    "vol_ratio": "volume_ratio",
    "volume_multiplier": "volume_ratio",
    "macd_histogram": "macd_hist",
    "histogram": "macd_hist",
    "hist": "macd_hist",
    "macd_line": "macd",
    "signal_line": "macd_signal",
    "price": "close",
    "supp": "support",
    "res": "resistance",
    "fib_50": "fib_5",
    "fib_500": "fib_5",
    "fib_05": "fib_5",
}


def _normalize_indicator_name(name: str) -> str:
    cleaned = str(name).strip().lower()
    return INDICATOR_ALIASES.get(cleaned, cleaned)


def _eval_condition(row, prev_row, cond: dict) -> bool:
    ind = _normalize_indicator_name(cond.get("indicator", ""))
    val = cond.get("value")
    op = cond.get("operator", "==")

    current_val = row.get(ind)
    if current_val is None or (isinstance(current_val, float) and current_val != current_val):
        return False

    target_val = None
    if isinstance(val, str):
        normalized_val_indicator = _normalize_indicator_name(val)
        if normalized_val_indicator in row and not (isinstance(row.get(normalized_val_indicator), float) and row.get(normalized_val_indicator) != row.get(normalized_val_indicator)):
            target_val = row.get(normalized_val_indicator)
        else:
            try:
                target_val = float(val)
            except (ValueError, TypeError):
                return False
    else:
        try:
            target_val = float(val)
        except (ValueError, TypeError):
            return False

    if target_val is None or (isinstance(target_val, float) and target_val != target_val):
        return False

    c_val = float(current_val)
    t_val = float(target_val)

    if op == "<":
        return c_val < t_val
    elif op == "<=":
        return c_val <= t_val
    elif op == ">":
        return c_val > t_val
    elif op == ">=":
        return c_val >= t_val
    elif op == "==":
        return abs(c_val - t_val) < 1e-6
    elif op == "!=":
        return abs(c_val - t_val) >= 1e-6
    return False


def _create_strategy_runner(def_dict: dict):
    long_conds = def_dict.get("longConditions", [])
    short_conds = def_dict.get("shortConditions", [])
    sl_mult = float(def_dict.get("slAtrMult", 1.5))
    rr = float(def_dict.get("riskRewardRatio", 2.5))
    use_trailing = bool(def_dict.get("useTrailingStop", False))
    trail_act = float(def_dict.get("trailActivationR", 1.0))
    trail_dist = float(def_dict.get("trailDistanceAtrMult", 1.5))

    def long_condition(row, prev_row, params):
        if not long_conds:
            return False
        return all(_eval_condition(row, prev_row, c if isinstance(c, dict) else c.dict()) for c in long_conds)

    def short_condition(row, prev_row, params):
        if not short_conds:
            return False
        return all(_eval_condition(row, prev_row, c if isinstance(c, dict) else c.dict()) for c in short_conds)

    def stop_target(direction, entry_price, atr, row, params):
        cur_sl_mult = params.get("CUSTOM_SL_ATR_MULT", sl_mult)
        cur_rr = params.get("CUSTOM_RISK_REWARD_RATIO", rr)
        stop_dist = cur_sl_mult * atr
        target_dist = stop_dist * cur_rr
        if direction == "LONG":
            return entry_price - stop_dist, entry_price + target_dist
        else:
            return entry_price + stop_dist, entry_price - target_dist

    def evaluate(df, params, long_rule, short_rule):
        p = dict(params, USE_TRAILING_STOP=use_trailing, TRAIL_ACTIVATION_R=trail_act,
                 TRAIL_DISTANCE_ATR_MULT=trail_dist)
        trades = simulate(df, p, long_rule, short_rule, stop_target)
        for trade in trades:
            trade["custom_exit_policy"] = {
                "enabled": use_trailing, "activation_r": trail_act, "distance_atr": trail_dist,
                "max_hold_bars": p["BACKTEST_MAX_HOLD_BARS"],
                "entry_open": trade["raw_entry_price"], "atr": trade["atr_at_signal"],
            }
        return trades

    def run(df, params):
        return evaluate(df, params, long_condition, short_condition)

    def run_inverse(df, params):
        return evaluate(df, params, short_condition, long_condition)

    return run, run_inverse
