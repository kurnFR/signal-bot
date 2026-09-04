# Website Improvement Recommendations
## Crypto Signal Bot — Web Dashboard Enhancements

**Goal**: Enable users to run all strategies and receive the top 3 best results based on most profitable strategy.

**Status**: Recommendations for code + UI improvements. No changes applied yet — review before acting.

---

## 1. New API Endpoint: `/api/backtest/run-all`

### Current State
The dashboard already has:
- `GET /api/backtest/strategies` — lists all strategies with metadata
- `POST /api/backtest/run` — runs a single strategy
- `GET /api/backtest/history` — retrieves past backtest runs

**Missing**: A convenience endpoint that runs ALL strategies and returns ranked results.

### Recommendation
Add a new route in `web/routes/backtest.py`:

```python
@router.post("/run-all")
def run_all_strategies(
    symbol: str = Query("BTCUSDT", example="BTCUSDT"),
    market: str = Query("spot", example="spot"),
    timeframe: str = Query("1d", example="1d"),
    data_segment: str = Query("full", example="full"),  # train, holdout, full
    initial_capital: float = Field(5000.0, ge=100.0),
    risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10.0),
):
    """
    Run ALL available strategies and return them ranked by profitability.
    
    Ranking order (primary to secondary):
    1. expectany_r — average R-multiple per trade (most important per PRD)
    2. profit_factor — gross winning R / gross losing R
    3. total_trades — number of trades (filter out thin results)
    4. win_rate_pct — win percentage
    
    Minimum trade count filter: 10 trades (configurable).
    """
    from backtest.params import BASE_PARAMS
    from backtest.metrics import compute_metrics
    from backtest.equity import simulate_equity_curve
    
    results = []
    
    for strategy_name in STRATEGIES.keys():
        try:
            # Build run params from BASE_PARAMS + shared execution params
            run_params = dict(BASE_PARAMS)
            run_params.update({
                "symbol": symbol,
                "market": market,
                "timeframe": timeframe,
                "data_segment": data_segment,
                "initial_capital": initial_capital,
                "risk_per_trade_pct": risk_per_trade_pct,
            })
            
            # Fetch data
            include_funding = strategy_name in FUNDING_STRATEGIES
            df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=include_funding)
            if len(df) < 50:
                continue  # skip if insufficient data
            
            # Apply data segment split
            cutoff = compute_holdout_cutoff(df, HOLDOUT_FRACTION) if data_segment in ("train", "holdout") else None
            if data_segment == "train":
                from backtest.holdout import split_by_cutoff
                train_df, _ = split_by_cutoff(df, cutoff)
                segment_df = train_df
            elif data_segment == "holdout":
                _, holdout_df = split_by_cutoff(df, cutoff)
                segment_df = holdout_df
            else:
                segment_df = df
            
            if len(segment_df) < 30:
                continue
            
            # Run strategy
            strategy_fn = STRATEGIES[strategy_name]
            trades = strategy_fn(segment_df, run_params)
            metrics = compute_metrics(trades)
            equity_res = simulate_equity_curve(trades, initial_capital, risk_per_trade_pct)
            
            results.append({
                "strategy": strategy_name,
                "displayName": STRATEGY_METADATA.get(strategy_name, {}).get("displayName", strategy_name),
                "category": STRATEGY_METADATA.get(strategy_name, {}).get("category", "Other"),
                "metrics": metrics,
                "equity": equity_res,
                "totalTrades": metrics["total_trades"],
            })
        except Exception as e:
            logger.error(f"Failed to run {strategy_name}: {e}")
            continue
    
    # Rank by: expectany_R (desc), profit_factor (desc), total_trades (desc, min 10)
    ranked = sorted(
        [r for r in results if r["metrics"]["total_trades"] >= 10],
        key=lambda x: (
            x["metrics"].get("expectancy_r", 0) or 0,
            x["metrics"].get("profit_factor", 0) or 0,
            x["metrics"].get("total_trades", 0) or 0,
        ),
        reverse=True,
    )
    
    return {
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "dataSegment": data_segment,
        "initialCapital": initial_capital,
        "riskPerTradePct": risk_per_trade_pct,
        "count": len(ranked),
        "rankedStrategies": ranked[:3],  # Top 3
        "allResults": ranked,  # All for reference
    }
```

### UI Integration
Add a new tab in the dashboard: **"Run All Strategies"** that:
- Shows a loading indicator while strategies are running
- Displays top 3 in a compact card with: strategy name, expectancy_R, profit_factor, total_trades, final equity
- Has a "View All" link to see complete results table
- Auto-refreshes every 60 seconds (or on manual reload)

---

## 2. Strategy Ranking Logic Improvements

### Current State
The `compute_metrics()` function in `backtest/metrics.py` computes:
- total_trades
- win_rate_pct
- expectancy_r
- profit_factor
- max_drawdown_r

### Recommendation
Enhance the ranking to give users clearer "most profitable" signal:

```python
def rank_strategy(metrics, total_trades):
    """
    Compute a composite score for strategy ranking.
    
    Weighting (configurable):
    - expectancy_r: 50% — the single most important number per PRD
    - profit_factor: 30% — above 1 = profitable aggregate
    - win_rate_pct: 15% — high win rate can mask negative expectancy
    - total_trades: 5% — filter out very thin results
    
    Returns (score, rank_qualifiers) tuple.
    """
    # Normalize scores to 0-1 range (approximate)
    ev_score = min(abs(metrics.get("expectancy_r", 0)) / 1.0, 1.0)  # assume ~1R max
    pf_score = min(metrics.get("profit_factor", 1) / 3.0, 1.0)  # assume ~3.0 max
    wr_score = min(metrics.get("win_rate_pct", 0) / 100.0, 1.0)
    tc_score = min(total_trades / 100, 1.0) if total_trades > 0 else 0
    
    composite = (ev_score * 0.5) + (pf_score * 0.3) + (wr_score * 0.15) + (tc_score * 0.05)
    return composite, {
        "expectancy_R": metrics.get("expectancy_r"),
        "profit_factor": metrics.get("profit_factor"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "total_trades": total_trades,
    }
```

Use this in the `run-all` endpoint to produce a single `rankScore` for each strategy, making it easier to present "best" in a single number.

---

## 3. New Dashboard Tab: "Strategy Battle Royale"

### UI Location
Add a new tab next to "Backtest" in the left panel navigation, labelled **"Battle Royale"** or **"Strategy Rank"**.

### UI Components
1. **Run All Strategies** button
   - When clicked: shows spinner, disables button
   - Fetches `/api/backtest/run-all` with default params (or user-configured)
   - Disables auto-while-spinner to prevent double-runs

2. **Top 3 Showcase Cards**
   - Card 1: `#1 Ranked` — strategy name, expectancy_R, profit_factor, total_trades, final equity
   - Card 2: `#2 Ranked` — same fields
   - Card 3: `#3 Ranked` — same fields
   - Each card has a "Run This Strategy" button that prefills the single-backtest form

3. **Full Results Table** (collapsible)
   - Columns: Strategy, Expectancy_R, Profit_Factor, Win_Rate_%, Total_Trades, Final_Equity_(USD)
   - Sortable by any column
   - "Run Selected" button for individual strategy backtest

4. **Parameter Configuration**
   - Before running: allow user to override BASE_PARAMS shared params (fee, slippage, max_hold, trailing stop)
   - Defaults from config.py are used if not overridden

5. **Status/Log Panel**
   - Real-time log stream while strategies run
   - Per-strategy status: "queued", "running", "completed", "failed"
   - Error details if a strategy fails (e.g., "insufficient candles", "division by zero in RSI")

---

## 3. Consistent Parameter Baseline

### Current State
Each strategy run starts from `BASE_PARAMS` in `config.py`, then user overrides via the `params` field. However:
- Not all strategies expose the same parameter knobs
- Some strategies hard-code their own params (e.g., `TREND_RISK_REWARD_RATIO` in trend_ema.py)
- The web form uses `STRATEGY_METADATA` to generate dynamic parameter UIs

### Recommendation
Ensure ALL strategy `run()` functions respect the `run_params` dict keys they receive. In `backtest/params.py`, consolidate the shared parameter defaults so every strategy's `run()` function pulls from a single source:

```python
# backtest/params.py
BASE_PARAMS = {
    "BACKTEST_FEE_PCT": 0.001,
    "BACKTEST_SLIPPAGE_PCT": 0.0005,
    "BACKTEST_MAX_HOLD_BARS": 200,
    "USE_TRAILING_STOP": False,
    "TRAIL_ACTIVATION_R": 1.0,
    "TRAIL_DISTANCE_ATR_MULT": 1.5,
    # Strategy-agnostic params
    "RSI_WINDOW": 14,       # if needed by any strategy
    "VOLATILITY_THRESHOLD_PCT": 0.0,  # default no filter
}
```

Then in each strategy's `run()`:
```python
def run(df, params):
    # Pull from params dict, fall back to None (engine default)
    fee = params.get("BACKTEST_FEE_PCT", 0.001)
    ...
```

This ensures the `run-all` endpoint produces comparable results across strategies.

---

## 4. Minimum Trade Count Filter

### Issue
Some strategies (especially mean-reversion on 15m/1h) may produce 1-3 trades over 2 years of data. Including these in "top 3" is misleading.

### Recommendation
In the `run-all` endpoint, enforce:
```python
MIN_TRADES_FOR_RANK = 10  # or 20
ranked = [r for r in results if r["metrics"]["total_trades"] >= MIN_TRADES_FOR_RANK]
```

Add a sidebar control: "Minimum Trades to Consider: [10__20__50__100]"

---

## 5. Sample Size Awareness

### Recommendation
Append a "Confidence" note to each ranked strategy:
- `if total_trades < 50: "⚠️ Few trades — results may not be statistically significant"`
- `if data_segment == "holdout": "✓ Holdout-validated"`
- `if data_segment == "train": "📓 Train-only — run holdout check to verify"`

Display this in the card footer or as a tooltip.

---

## 6. Example: How the Feature Would Work End-to-End

### User Flow
1. User opens dashboard → logs in
2. Clicks **"Strategy Rank"** tab in left panel
3. Sees: "Run All Strategies — 10 strategies available"
4. Clicks **Run All** button
5. Progress spinner shows:
   - "Running confluence_ensemble_v1... ✓ (42 trades, expectancy +0.15R)"
   - "Running trend_ema_v1... ✓ (114 trades, expectancy +0.21R)"
   - "Running breakout_continuation_v1... ✓ (900 trades, expectancy -0.35R)" [FAIL: negative expectancy]
   - "..."
6. Results appear:
   - **#1**: `trend_ema_v1` (BTC 4h) — expectancy +0.21R, PF 1.28, 114 trades, final equity $5,842
   - **#2**: `confluence_ensemble_v1` (ETH 1d) — expectancy +0.49R (holdout), PF 1.85, 38 trades, final equity $5,491
   - **#3**: `smc_liquidity_sweep_v1` (BNB 1d) — expectancy +0.55R, PF 2.13, 24 trades, final equity $5,678
7. User clicks "Run #1 strategy" → prefills backtest form with those params → runs single backtest for closer inspection

---

## 7. Priority Implementation Plan

| Priority | Feature | Estimated Effort | Impact |
|----------|---------|-----------------|--------|
| **P0** | `POST /api/backtest/run-all` endpoint | ~2-3 hours | Core functionality |
| **P0** | Minimum trade count filter + ranking logic | ~1 hour | Data quality |
| **P1** | "Strategy Battle Royale" dashboard tab + UI | ~4-6 hours | User experience |
| **P1** | Consistent BASE_PARAMS across all strategies | ~2-3 hours | Result comparability |
| **P2** | Minimum trade count UI control | ~1 hour | User control |
| **P2** | Confidence/sample size notes on cards | ~1 hour | Educational value |
| **P3** | Auto-refresh / periodic re-run | ~1 hour | Keep results fresh |

**Total estimated**: ~15-20 hours for full feature set.

---
*No code changes have been applied. This document is for planning and review. The existing `/api/backtest/run` endpoint remains functional for single-strategy backtests.*