# Backtest cost sensitivity

Use the cost sensitivity report to see whether a strategy's training-segment results depend on optimistic fixed fee/slippage assumptions.

## Example

```bash
python -m backtest.cost_sensitivity \
  --symbol BTCUSDT --market spot --timeframe 1h \
  --strategy breakout_continuation_v1
```

The command prints CSV to stdout for every combination of the configured fee and slippage rates. Rates are decimal fractions **per side**: `0.001` means 0.1%. Customize the scenarios, for example:

```bash
python -m backtest.cost_sensitivity \
  --symbol BTCUSDT --market spot --timeframe 1h \
  --strategy breakout_continuation_v1 \
  --fees 0.0005,0.001,0.002 \
  --slippages 0,0.0005,0.001
```

## Safety and interpretation

- The command uses the same strategy, candles, and training segment for each scenario; the reserved holdout segment is not evaluated.
- It prints results only. It does not insert/update backtest records, change configuration, or modify database state.
- Existing strategy defaults are copied and overridden in memory for each run; they are not mutated.
- These are constant per-side fee and slippage scenarios. They do **not** model bid/ask spread, order-book depth, volume participation, market impact, latency, or venue-specific fee tiers.
- Sensitivity is a robustness check, not evidence of future profitability. Do not select a strategy based on repeated holdout evaluation.
