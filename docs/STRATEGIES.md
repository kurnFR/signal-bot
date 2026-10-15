# Strategies

The strategy library, the validation workflow, and the results that passed holdout. Rules behind the workflow are in [ARCHITECTURE.md](ARCHITECTURE.md#8-engineering-rules).

## 1. Library

12 base strategies live in `backtest/strategies/`. Each also has an `inverse_<name>` variant (long and short swapped), registered automatically, so the registry holds **24** runnable names. All share `backtest/simulate.py` (next-bar-open entry, ATR stop/target, optional trailing stop, fees and slippage).

| `NAME` | Idea | Extra data it needs |
|---|---|---|
| `confluence_reversal_v1` | Fade exhaustion: RSI extreme, price at support/resistance, elevated volume, MACD turning | none |
| `breakout_continuation_v1` | Trade with a decisive break of a rolling high/low | none |
| `trend_ema_v1` | Fast/slow EMA crossover confirmed by MACD | none |
| `trend_alignment_v1` | EMA crossover only when the higher-timeframe (daily) trend agrees | higher-timeframe join (not for the daily timeframe itself) |
| `fibonacci_retracement_v1` | Pullback into the 61.8–78.6% zone of an EMA-defined trend | none |
| `smc_liquidity_sweep_v1` | Wick sweeps beyond a swing high/low, then rejects back inside | none |
| `supply_demand_v1` | Base candle plus displacement marks a zone; trade the retest | none |
| `double_pattern_v1` | Double top/bottom with neckline-break confirmation | none |
| `funding_extreme_reversal_v1` | Fade crowded positioning when futures funding is extreme | funding rate |
| `confluence_ensemble_v1` | Weighted vote of six components (RSI, trend, MACD, volume, S/R, funding) | funding rate |
| `pairs_ratio_v1` | Z-score of the price ratio to a base leg (default `BTCUSDT`) reverting to its mean | second symbol's data |
| `volatility_breakout_v1` | Breakouts filtered by expanding volatility and a trend regime | none |

Custom strategies can also be defined as JSON conditions (indicator, operator, value) from the dashboard; they are stored in `custom_strategies.json`.

## 2. Guidance

- **Use 4h and 1d.** On 15m and 1h, ATR-sized stops are often similar in size to round-trip costs, so costs consume most of any edge. The timeframes stay in the data for completeness; do not expect them to validate.
- **A profitable train result is not evidence.** Parameter optimization on train data regularly makes results look better and then fails holdout. Treat holdout as the test, not the optimizer.
- **Only the strategies in section 4 have passed holdout.** The rest are implemented and usable for research, but have no validated edge; deploying them to paper trading is exploration, not confirmation.

## 3. Validation workflow

Run these in order for each candidate (symbol, timeframe, market, strategy). Research commands only ever read the oldest 80% of history.

```bash
# 1. Explore on train
python -m backtest.run_backtest --symbol ETHUSDT --timeframe 1d --market spot --strategy confluence_ensemble_v1
python -m backtest.report       --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1
python -m backtest.pnl_report   --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --capital 5000 --risk-pct 1

# 2. Optional parameter search, then confirm a specific combination is stable across folds
python -m backtest.optimize     --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --min-trades 20 --top-n 10
python -m backtest.walk_forward --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --folds 4 --override KEY=VALUE

# 3. Single-use holdout check. Only after the combination is fully decided.
python -m backtest.run_holdout_check --symbol ETHUSDT --timeframe 1d --strategy confluence_ensemble_v1 --confirm
```

What to look for in walk-forward: the edge should hold, ideally strengthen, into the most recent fold. A strong middle with a collapsing latest fold is a warning sign, and a candidate showing it should not be spent on a holdout check.

The dashboard's **Run all strategies** (Battle Royale) ranks every variant on one symbol/timeframe/segment, requiring positive expectancy and profit factor of at least 1.0 to reach the podium. That is a screening aid; it does not replace the holdout check.

## 4. Results that passed holdout

| Strategy | Market | Result on the holdout segment |
|---|---|---|
| `confluence_ensemble_v1` | `ETHUSDT` spot, 1d | +0.494 R average, 9 trades, 44.4% win rate, profit factor 1.85 |
| `pairs_ratio_v1` | `BNBUSDT` futures, 1d (`PAIRS_ZSCORE_LOOKBACK_BARS=50`, `PAIRS_ZSCORE_ENTRY_THRESHOLD=1.5`, trailing stop on, `TRAIL_ACTIVATION_R=1.0`, `TRAIL_DISTANCE_ATR_MULT=2.0`) | +0.711 R average, 16 trades, 43.75% win rate, profit factor 2.10, max drawdown 6.67 R. Walk-forward profitable in 4 of 4 folds |

These are small samples measured on the holdout window at the time. Treat them as candidates to monitor in paper trading, not proof, and re-validate as new data arrives (the holdout window is the newest 20% and shifts as history grows).

## 5. Adding a strategy

1. Create `backtest/strategies/<name>.py` with a `NAME` constant, `run(df, params) -> list[trade]` and `run_inverse(df, params)`, delegating execution to `simulate()`.
2. Add the module to `_MODULES` in `backtest/strategies/__init__.py`. The inverse variant and the CLI/UI choices follow automatically.
3. Put new tunables in `backtest/params.py` (`BASE_PARAMS`) and `config.py`.
4. If it needs funding, a higher timeframe, or a second symbol, add its name to `FUNDING_STRATEGIES`, `TREND_ALIGNMENT_STRATEGIES` or `PAIRS_STRATEGIES` in the same `__init__.py`. That data join is applied separately in `run_backtest.py`, `walk_forward.py`, `optimize.py`, `run_holdout_check.py`, `web/routes/backtest.py` and `paper/engine.py`, so check each place handles the new name. Missing it makes the extra-data component silently contribute nothing.
5. Test it for lookahead (mutate future bars, recompute, assert earlier trades are identical) and with a hand-built fixture before trusting any real-data result.
