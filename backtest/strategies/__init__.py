"""
Strategy registry. Add a new strategy by creating a module in this
package with a NAME constant and a run(df, params) -> list[trade dict]
function, then register it here.

This registry is what a future UI (symbol/timeframe/strategy picker) would
also import to list available strategies -- keeping it here now means
that integration point already exists.
"""
from backtest.strategies import (
    mean_reversion, breakout, trend_ema, trend_alignment,
    fibonacci_retracement, smc_liquidity_sweep, supply_demand, double_pattern,
    funding_extreme, confluence_ensemble, pairs_ratio, volatility_breakout,
)

_MODULES = [
    mean_reversion, breakout, trend_ema, trend_alignment,
    fibonacci_retracement, smc_liquidity_sweep, supply_demand, double_pattern,
    funding_extreme, confluence_ensemble, pairs_ratio, volatility_breakout,
]

STRATEGIES = {m.NAME: m.run for m in _MODULES}

# Every strategy module also exposes run_inverse() (wherever the original
# would go LONG, the inverse goes SHORT, and vice versa, using the same
# already-direction-aware stop sizing -- see any strategy module for
# details). Registered as "inverse_<name>" so they show up in
# run_backtest.py/report.py/optimize.py exactly like any other strategy,
# for direct PnL comparison against the original.
for m in _MODULES:
    STRATEGIES[f"inverse_{m.NAME}"] = m.run_inverse

# Strategies that need the funding_rate column joined onto their input df
# (see db.fetch_ohlcv_with_features_df's include_funding param). Scripts
# that fetch data for a strategy check this set to know whether to pass
# include_funding=True. confluence_ensemble uses funding optionally (its
# funding component gracefully contributes 0 if unavailable) but still
# benefits from having it when possible.
FUNDING_STRATEGIES = {
    funding_extreme.NAME, confluence_ensemble.NAME,
    f"inverse_{funding_extreme.NAME}", f"inverse_{confluence_ensemble.NAME}",
}

# trend_alignment and its inverse both need a higher-timeframe df joined in
# (see backtest/strategies/trend_alignment.py) -- scripts that special-case
# this check membership in this set rather than comparing to one exact name.
TREND_ALIGNMENT_STRATEGIES = {trend_alignment.NAME, f"inverse_{trend_alignment.NAME}"}

# pairs_ratio and its inverse need a second symbol's df (the base/quote
# leg) joined in -- see backtest/strategies/pairs_ratio.py.
PAIRS_STRATEGIES = {pairs_ratio.NAME, f"inverse_{pairs_ratio.NAME}"}
