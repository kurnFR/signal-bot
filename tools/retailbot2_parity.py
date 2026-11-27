"""Compare RetailBot2 signal decisions before applying paper execution rules.

Run from the repository root with python -m tools.retailbot2_parity.
"""
import argparse
import math
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest.params import BASE_PARAMS, RETAILBOT2_DEFAULT_PARAMS
from backtest.strategies import retailbot2_strategies as library
from paper.retailbot2 import BotConfig, RetailDeathTrapBot, RetailStrategyEngine

ORACLE_METHODS = {
    "BB_REV": "strat_bband_reversion",
    "RSI_CTR": "strat_rsi_counter_trend",
    "MA_WHIP": "strat_ma_crossover",
    "SPIKE_FADE": "strat_spike_fade",
    "SR_TRAP": "strat_sr_bounce",
    "MACD_DIV": "strat_macd_divergence",
    "DBL_PAT": "strat_double_pattern",
}


def candles(seed, count=768):
    rng = np.random.default_rng(seed)
    candles_no = np.arange(count)
    close = 100 + 0.008 * candles_no + 1.3 * np.sin(candles_no / 8)
    close += rng.normal(0, 0.25, count)
    opening = np.r_[close[0], close[:-1]] + rng.normal(0, 0.12, count)
    opening[::19] += rng.choice([-2.5, 2.5], len(opening[::19]))
    return pd.DataFrame({
        "open_time": candles_no * 3_600_000,
        "open": opening,
        "close": close,
        "high": np.maximum(opening, close) + rng.uniform(0.05, 0.2, count),
        "low": np.minimum(opening, close) - rng.uniform(0.05, 0.2, count),
    })


def compare_frames(frames, stride=1, mode="engine"):
    """Compare each registered runner's direction and signal-close risk across frames."""
    params = dict(BASE_PARAMS)
    if mode == "live":
        params.update(RETAILBOT2_DEFAULT_PARAMS)
    result = {
        f"{name}{suffix}": np.zeros(5, dtype=int)
        for name in ORACLE_METHODS for suffix in ("", "_INV")
    }
    oracle = RetailStrategyEngine(BotConfig())
    for frame in frames:
        oracle_frame = oracle.calculate_indicators(frame)
        for base_name, method in ORACLE_METHODS.items():
            oracle_signal = getattr(oracle, method)
            for suffix in ("", "_INV"):
                name = base_name + suffix
                counts = result[name]

                def capture(prepared, effective_params, buy, sell, stop_target):
                    for i in range(1, len(prepared) - 1, stride):
                        live = oracle_signal(oracle_frame.iloc[:i + 2], "TEST")
                        if live and suffix:
                            live = RetailDeathTrapBot._invert(live)
                        row, previous = prepared.iloc[i], prepared.iloc[i - 1]
                        side = "LONG" if buy(row, previous, effective_params) else (
                            "SHORT" if sell(row, previous, effective_params) else None
                        )
                        counts[:4] += [1, bool(live), bool(side), side != (live and live["side"])]
                        if live and side == live["side"]:
                            stop, target = stop_target(side, row["close"], row["atr"], row, effective_params)
                            if not (math.isclose(stop, live["stop"]) and math.isclose(target, live["target"])):
                                counts[4] += 1
                    return []

                with patch.object(library, "simulate", capture):
                    library.RETAILBOT2_STRATEGIES[name](frame, params)
    return result


def compare(seeds, stride=1, mode="engine", count=768):
    """Compare each registered runner's direction and signal-close risk on synthetic seeds."""
    return compare_frames((candles(seed, count) for seed in seeds), stride=stride, mode=mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seeds", type=lambda values: [int(value) for value in values.split(",")])
    parser.add_argument("stride", type=int)
    parser.add_argument("mode", choices=("engine", "live"))
    args = parser.parse_args()
    if args.stride < 1:
        parser.error("stride must be positive")
    report = compare(args.seeds, args.stride, args.mode)
    print("Strategy       Bars   Live Library Direction Stop/target")
    for strategy, counts in report.items():
        print(f"{strategy:14}", " ".join(f"{value:6}" for value in counts))
    return int(any(counts[3] or counts[4] for counts in report.values()))


if __name__ == "__main__":
    raise SystemExit(main())
