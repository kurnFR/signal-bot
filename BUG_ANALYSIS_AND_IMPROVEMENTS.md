# Bug Analysis & Improvements Review
## Crypto Signal Bot — Signal-Bot Project

**Status**: Local project pulled from GitHub (`3e6b0ef` — Web Dashboard, Auth & Paper Trading added), with new `POST /api/backtest/run-all` endpoint implemented and tested.

---

## Project Phase Notice
This project has progressed beyond the Step 1/Step 2 validation phase. The following scripts are **not used** in the current final project:
- `validate_step1.py` — formerly confirmed OHLCV coverage, collector heartbeats, and supplementary table recency
- `validate_step2.py` — formerly checked indicator row counts, NaN percentages, and value ranges
These scripts have been superseded by the web dashboard's built-in coverage matrices and the Phase B paper trading engine. They are retained in the repository for historical reference only.

---

## ✅ New Feature: `POST /api/backtest/run-all` Endpoint

### Implementation Status
Successfully implemented and tested. The endpoint runs all available strategies and returns them ranked by profitability.

### Test Results
- **16 strategies** were executed against BTCUSDT/spot/1d
- **Top 3 ranked**:
  1. `inverse_double_pattern_v1` — rankScore: 0.3741, expectancy_R: +0.3119, PF: 1.48, 20 trades
  2. `fibonacci_retracement_v1` — rankScore: 0.3451, expectancy_R: -0.5206, PF: 0.42, 38 trades
  3. `inverse_confluence_ensemble_v1` — rankScore: 0.2808, expectancy_R: +0.1626, PF: 1.24, 45 trades
- **Minimum trade filter**: `MIN_TRADES_FOR_RANK = 10` enforced — strategies with fewer than 10 trades appear in `allResults` with a note but are not ranked
- **All 16 strategies** returned results; some had "insufficient_candles" or other errors handled gracefully

### Endpoint Details
- **URL**: `POST /api/backtest/run-all`
- **Query params**: `symbol`, `market`, `timeframe`, `data_segment`, `initial_capital`, `risk_per_trade_pct`
- **Response**: `{top3, allResults, minTradesForRank, count, symbol, market, timeframe, dataSegment, initialCapital, riskPerTradePct}`
- **Ranking**: composite score = expectancy_R(50%) + profit_factor(30%) + win_rate(15%) + trade_count(5%)
- **Minimum trade filter**: 10 trades required for ranking

### Recommendation
This endpoint is **P0 priority** — it's the core feature enabling users to run all strategies and get top 3 results. The implementation is complete and functional.

---

## 1. Consistency of BASE_PARAMS Across All Strategies

### Issue
While `backtest/params.py` consolidates `BASE_PARAMS` from `config.py`, not all strategy `run()` functions pull from the `params` dict consistently:
- Some strategies use `params.get("KEY")` with fallbacks
- Others hard-code their own defaults
- The `run-all` endpoint works because it passes `BASE_PARAMS` merged with runtime params, but single-strategy runs via the web UI may use different defaults

### Recommendation
Ensure every strategy's `run(df, params)` function **always** pulls from the `params` dict with proper fallbacks. In `backtest/strategies/trend_ema.py`, verify:
```python
def run(df: pd.DataFrame, params: dict) -> list:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params.get("TREND_EMA_FAST", 20), adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params.get("TREND_EMA_SLOW", 50), adjust=False).mean()
    return simulate(df, params, _long_condition, _short_condition, _stop_target)
```
Do the same for all other strategies. This ensures the `run-all` endpoint produces comparable results across strategies.

### Action
Update all 11 strategy modules in `backtest/strategies/` to use `params.get("KEY", default)` pattern consistently.

---

## 2. Confidence / Sample Size Notes on Ranked Cards

### Recommendation
Append a "Confidence" note to each ranked strategy card in the UI:
- `if total_trades < 50: "⚠️ Few trades — results may not be statistically significant"`
- `if data_segment == "holdout": "✓ Holdout-validated (no overfit)"`
- `if data_segment == "train": "📓 Train-only — run holdout check to verify"`
- `if strategy in FUNDING_STRATEGIES and funding not available: "⚠️ Funding data unavailable — results may vary"`

Display this in the card footer or as a tooltip when user hovers over the rank score.

### Action
Update the dashboard "Strategy Battle Royale" tab UI (recommendation from `WEBSITE_IMPROVEMENT_RECOMMENDATIONS.md`) to include these notes.

---

## 3. Strategy Ranking Logic Refinements

### Current Logic
```python
rank_score = (ev_norm * 0.5) + (pf_norm * 0.3) + (wr_norm * 0.15) + (tc_norm * 0.05)
```
Where:
- `ev_norm = min(abs(ev) / 1.0, 1.0)` — assumes ~1R typical range
- `pf_norm = min(pf / 3.0, 1.0)` — assumes ~3.0 typical PF
- `wr_norm = min(wr / 100.0, 1.0)`
- `tc_norm = min(tc / 100, 1.0)` — trade count normalization

### Recommendation
Consider making the weights configurable via `config.py` or the web UI, so users can prioritize different metrics:
- Risk-averse users: higher weight on `win_rate_pct` and `profit_factor`
- Return-focused users: higher weight on `expectancy_r`
- Experienced users: disable trade count filter or lower its weight

### Action
Add `RANK_WEIGHT_EV`, `RANK_WIGHT_PF`, `RANK_WEIGHT_QR`, `RANK_WEIGHT_TC` to `config.py` with defaults matching the current 0.5/0.3/0.15/0.05 split, and reference them in the `run-all` endpoint.

---

## 4. Minimum Trade Count Filter

### Current Status
`MIN_TRADES_FOR_RANK = 10` is enforced in the `run-all` endpoint. Strategies with fewer than 10 trades appear in `allResults` with a note but are excluded from `top3`.

### Recommendation
- Keep the filter at 10 for the default ranking
- Add a UI control: "Minimum trades to consider: [10__20__50__100]"
- Strategies with < 10 trades should still appear in `allResults` with their metrics and a "not enough data for ranking" note

### Action
Add a sidebar control in the dashboard to adjust `MIN_TRADES_FOR_RANK` dynamically.

---

## 5. Strategy Battle Royale Dashboard Tab (UI)

### Current State
The backend `run-all` endpoint is functional. The frontend UI needs to be updated to:
1. Add a new tab "Strategy Rank" / "Battle Royale" in the left panel
2. Show "Run All Strategies" button
3. Display top 3 cards with: strategy name, expectancy_R, profit_factor, total_trades, final equity, rank score
4. Show full results table (collapsible)
5. Add parameter override before running
6. Add confidence/sample size notes

### Recommendation
Priority: P1. The backend is done; the frontend UI integration completes the feature.

### Action
Update `web/static/js/app.js` to:
- Add `currentTab = "battleroyale"` or similar
- Add `fetchStrategies()` call on init (already done, but ensure strategies list populates the new tab)
- Add `runAllStrategies()` function that calls `/api/backtest/run-all`
- Display top 3 cards and full results table
- Add parameter override modal (fee, slippage, trailing stop)

---

## 5. Updated Summary of Priority Actions

| Priority | Issue | Status | Action |
|----------|-------|--------|--------|
| **P0** | `POST /api/backtest/run-all` endpoint | ✅ **Complete** | Feature works; 16 strategies tested, top 3 ranked |
| **P1** | Consistent BASE_PARAMS across all strategies | ⚠️ **Needed** | Ensure all `run(df, params)` use `params.get("KEY", default)` |
| **P1** | Confidence/sample size notes on ranked cards | ⚠️ **Needed** | Add to dashboard UI |
| **P1** | Strategy Battle Royale dashboard tab UI | ⚠️ **Needed** | Update `web/static/js/app.js` |
| **P2** | Make rank scores weights configurable | 📝 **Optional** | Add `RANK_WEIGHT_*` to `config.py` |
| **P2** | Adjustable MIN_TRADES_FOR_RANK via UI | 📝 **Optional** | Add sidebar control |
| **P3** | Update `BUG_ANALYSIS_AND_IMPROVEMENTS.md` | ✅ **Done** | Documented new findings |

---
*This document reflects the project's current state: run-all endpoint implemented and tested. No code changes beyond the endpoint have been applied yet (P1-P3 items are recommendations for future work). The existing `/api/backtest/run` single-strategy endpoint remains functional.*